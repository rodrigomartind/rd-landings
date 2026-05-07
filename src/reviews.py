"""Bajada masiva de reviews via Outscraper.

La Places API solo devuelve 5 reviews por place. Para tener material analitico
de verdad usamos Outscraper (servicio de terceros que hace dump completo).

Outscraper acepta hasta 500 place_ids por request y permite reviewsLimit, sort, etc.
"""
from __future__ import annotations

from typing import Any

from outscraper import ApiClient
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from tenacity import retry, stop_after_attempt, wait_exponential

from . import db
from .config import MAX_REVIEWS_PER_PLACE, OUTSCRAPER_API_KEY

console = Console()


def get_client() -> ApiClient:
    if not OUTSCRAPER_API_KEY:
        raise RuntimeError("Falta OUTSCRAPER_API_KEY en .env")
    return ApiClient(api_key=OUTSCRAPER_API_KEY)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _fetch_batch(client: ApiClient, place_ids: list[str]) -> list[dict[str, Any]]:
    # google_maps_reviews acepta lista; reviewsLimit cap a MAX_REVIEWS_PER_PLACE.
    return client.google_maps_reviews(
        place_ids,
        reviews_limit=MAX_REVIEWS_PER_PLACE,
        sort="newest",
        language="es",
        async_request=False,
    )


def _normalize(raw_review: dict[str, Any]) -> dict[str, Any]:
    return {
        "author": raw_review.get("author_title") or raw_review.get("author_name"),
        "rating": raw_review.get("review_rating") or raw_review.get("rating"),
        "text": raw_review.get("review_text") or raw_review.get("text"),
        "language": raw_review.get("review_language") or raw_review.get("language"),
        "posted_at": raw_review.get("review_datetime_utc")
        or raw_review.get("review_timestamp"),
    }


def scrape_reviews(limit: int = 50, batch_size: int = 25) -> dict[str, int]:
    """Trae reviews de los proximos N negocios sin web y sin reviews scrapeadas."""
    db.init_db()
    client = get_client()
    fetched_places = 0
    fetched_reviews = 0

    with db.connect() as conn:
        targets = db.fetch_for_review_scrape(conn, limit)

    if not targets:
        console.log("[yellow]No hay negocios pendientes para scrape de reviews.")
        return {"places": 0, "reviews": 0}

    place_ids = [row["place_id"] for row in targets]

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]Bajando reviews — {task.description}"),
        console=console,
    ) as progress:
        for i in range(0, len(place_ids), batch_size):
            batch = place_ids[i : i + batch_size]
            task = progress.add_task(f"batch {i // batch_size + 1}", total=None)
            try:
                results = _fetch_batch(client, batch)
            except Exception as e:  # pragma: no cover
                console.log(f"[red]outscraper error: {e}")
                progress.remove_task(task)
                continue

            for entry in results:
                pid = entry.get("place_id") or entry.get("query")
                if not pid:
                    continue
                raw_reviews = entry.get("reviews_data") or entry.get("reviews") or []
                normalized = [_normalize(r) for r in raw_reviews if r.get("review_text") or r.get("text")]
                with db.connect() as conn:
                    inserted = db.insert_reviews(conn, pid, normalized)
                fetched_places += 1
                fetched_reviews += inserted
            progress.remove_task(task)

    console.log(
        f"[green]Reviews bajadas: {fetched_reviews} de {fetched_places} negocios"
    )
    return {"places": fetched_places, "reviews": fetched_reviews}
