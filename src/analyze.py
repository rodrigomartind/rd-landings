"""Analisis de reviews con Claude (structured outputs + prompt caching).

El system prompt es estable -> cacheable. Cada negocio cambia solo el user
message. Esto baja costo ~80% en corridas grandes.

Salida JSON con schema fijo:
{
  "summary": str,
  "strengths": [str, ...],
  "pains": [str, ...],
  "angles": [str, ...],
  "tone": str,
  "target_audience": str,
  "keywords": [str, ...]
}
"""
from __future__ import annotations

import json
from typing import Any

import anthropic
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from tenacity import retry, stop_after_attempt, wait_exponential

from . import db
from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL

console = Console()

SYSTEM_PROMPT = """Sos un analista experto en marketing digital y comportamiento del consumidor en Argentina, especializado en negocios locales (PyMEs, comercios de barrio, profesionales independientes).

Tu tarea: dado un negocio y sus reseñas de Google Maps, extraer materia prima estructurada para construir una landing page que potencie sus fortalezas y contrarreste activamente sus debilidades.

Reglas:
1. Trabajá SOLO con evidencia de las reseñas. Nunca inventes hechos.
2. Las "pains" son patrones repetidos en reseñas negativas (no quejas aisladas). Si solo hay una queja sobre algo, no la incluyas.
3. Los "angles" son ganchos accionables para la landing — frases concretas que se podrían usar como copy, no descripciones genéricas.
4. El "tone" recomendado se infiere del público y categoría (ej: "cercano y familiar", "profesional y técnico", "premium y exclusivo").
5. El "target_audience" es el cliente real según las reseñas (no el ideal teórico).
6. Las "keywords" son términos SEO locales accionables (categoría + zona + diferenciador).
7. Si hay menos de 3 reseñas con texto, marcá summary como "datos insuficientes" y dejá los arrays vacíos.

Respondé SIEMPRE con JSON válido que matchee el schema solicitado. Sin texto antes ni después."""


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Resumen ejecutivo en 2-3 oraciones del estado actual del negocio segun reviews.",
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Fortalezas recurrentes mencionadas en reviews positivas. Especificas, no genericas.",
        },
        "pains": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Patrones repetidos de quejas en reviews negativas. Solo patrones (>=2 menciones).",
        },
        "angles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ganchos copy-ready para la landing que potencien fortalezas y contrarresten pains.",
        },
        "tone": {
            "type": "string",
            "description": "Tono recomendado para la landing. Ej: 'cercano y familiar'.",
        },
        "target_audience": {
            "type": "string",
            "description": "Cliente real segun reviews, no el ideal teorico.",
        },
        "keywords": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Keywords SEO locales accionables (categoria + zona + diferenciador).",
        },
    },
    "required": [
        "summary",
        "strengths",
        "pains",
        "angles",
        "tone",
        "target_audience",
        "keywords",
    ],
    "additionalProperties": False,
}


def get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en .env")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _build_user_message(biz: dict[str, Any], reviews: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append(f"# Negocio: {biz['name']}")
    lines.append(f"Categoria: {biz['category']}")
    lines.append(f"Zona: {biz['zone']}")
    if biz.get("address"):
        lines.append(f"Direccion: {biz['address']}")
    if biz.get("rating") is not None:
        lines.append(
            f"Rating: {biz['rating']} ({biz.get('reviews_count') or 0} reviews totales)"
        )
    lines.append("")
    lines.append(f"# Reviews ({len(reviews)} con texto)")
    for i, r in enumerate(reviews, 1):
        rating = r.get("rating") or "-"
        text = (r.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"\n[{i}] Rating: {rating}/5")
        lines.append(text)
    lines.append("")
    lines.append("Devolveme el JSON con el analisis siguiendo el schema.")
    return "\n".join(lines)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def analyze_business(
    client: anthropic.Anthropic,
    biz: dict[str, Any],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    user_msg = _build_user_message(biz, reviews)

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}
        },
        messages=[{"role": "user", "content": user_msg}],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise RuntimeError("Respuesta vacia del modelo")
    payload = json.loads(text)

    # Log cache hit para verificar que el caching funciona
    usage = response.usage
    console.log(
        f"[dim]tokens: in={usage.input_tokens} cache_read={usage.cache_read_input_tokens} "
        f"cache_create={usage.cache_creation_input_tokens} out={usage.output_tokens}"
    )
    return payload


def analyze_pending(limit: int = 50, min_reviews: int = 3) -> dict[str, int]:
    db.init_db()
    client = get_client()

    with db.connect() as conn:
        targets = db.fetch_for_analysis(conn, limit)

    if not targets:
        console.log("[yellow]No hay negocios pendientes de analisis.")
        return {"analyzed": 0, "skipped": 0}

    analyzed = 0
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        console=console,
    ) as progress:
        for row in targets:
            biz = dict(row)
            with db.connect() as conn:
                reviews = [dict(r) for r in db.fetch_reviews(conn, biz["place_id"])]

            usable = [r for r in reviews if (r.get("text") or "").strip()]
            if len(usable) < min_reviews:
                console.log(
                    f"[yellow]skip {biz['name']}: solo {len(usable)} reviews con texto"
                )
                skipped += 1
                continue

            task = progress.add_task(f"Analizando {biz['name']}", total=None)
            try:
                payload = analyze_business(client, biz, usable)
            except Exception as e:  # pragma: no cover
                console.log(f"[red]error analizando {biz['name']}: {e}")
                progress.remove_task(task)
                continue

            with db.connect() as conn:
                db.save_analysis(
                    conn, biz["place_id"], payload, ANTHROPIC_MODEL, len(usable)
                )
            analyzed += 1
            progress.remove_task(task)

    console.log(
        f"[green]Analizados: {analyzed}  |  Saltados (pocos reviews): {skipped}"
    )
    return {"analyzed": analyzed, "skipped": skipped}
