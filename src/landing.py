"""Generador de landing pages con Claude.

Pipeline por landing:
1. Pull negocio + analisis + top reviews desde DB
2. Construir payload JSON
3. Llamar a Claude (Opus 4.7 + adaptive thinking + effort high)
4. Guardar HTML en clients/<slug>/index.html
5. Persistir metadata + costo en DB landings
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

import anthropic
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from . import db
from .config import (
    ANTHROPIC_API_KEY,
    CLIENTS_DIR,
    LANDING_BRAND,
    LANDING_MODEL,
    LANDING_WHATSAPP,
    LANDING_WHATSAPP_DISPLAY,
)
from .landing_prompts import SYSTEM_PROMPT, build_user_message

console = Console()

# Pricing por 1M tokens — Opus 4.7
PRICING = {
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_create": 6.25},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
}


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    s = re.sub(r"-+", "-", s)
    return s[:60] or "negocio"


def get_client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("Falta ANTHROPIC_API_KEY en .env")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _calc_cost(usage: dict[str, int], model: str) -> float:
    p = PRICING.get(model, PRICING["claude-opus-4-7"])
    return (
        usage.get("input_tokens", 0) * p["input"] / 1_000_000
        + usage.get("output_tokens", 0) * p["output"] / 1_000_000
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"] / 1_000_000
        + usage.get("cache_creation_input_tokens", 0) * p["cache_create"] / 1_000_000
    )


def build_payload(
    business: dict[str, Any],
    analysis: dict[str, Any],
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Arma el payload JSON que va al user message del prompt."""
    # Parse JSON arrays del analisis (vienen como strings desde SQLite)
    def _maybe_json(v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return v
        return v

    # Hours puede venir como JSON string
    hours = _maybe_json(business.get("hours")) or []

    payload = {
        "business": {
            "name": business["name"],
            "category": business["category"],
            "zone": business["zone"],
            "address": business.get("address"),
            "lat": business.get("lat"),
            "lng": business.get("lng"),
            "phone": business.get("phone"),
            "hours": hours if isinstance(hours, list) else [],
            "rating": business.get("rating"),
            "reviews_count": business.get("reviews_count"),
            "website_kind": business.get("website_kind"),
        },
        "analysis": {
            "summary": analysis.get("summary"),
            "strengths": _maybe_json(analysis.get("strengths")) or [],
            "pains": _maybe_json(analysis.get("pains")) or [],
            "angles": _maybe_json(analysis.get("angles")) or [],
            "tone": analysis.get("tone"),
            "target_audience": analysis.get("target_audience"),
            "keywords": _maybe_json(analysis.get("keywords")) or [],
        },
        "top_reviews": [
            {
                "author": r["author"],
                "rating": r["rating"],
                "text": r["text"],
            }
            for r in reviews
        ],
        "brand_footer": f"Una propuesta de {LANDING_BRAND} · agendá una llamada",
        "whatsapp": {
            "number": LANDING_WHATSAPP,
            "display": LANDING_WHATSAPP_DISPLAY,
            "prefilled": (
                f"Hola {(LANDING_BRAND or '').split()[0]}! Vi la landing que armaste para "
                f"{business['name']} y quiero charlar."
            ),
        },
    }
    return payload


def _strip_html_fences(text: str) -> str:
    """Por si Claude devuelve ```html ... ``` aunque le pedimos que no."""
    text = text.strip()
    if text.startswith("```"):
        # remove first fence line
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


def generate_landing(
    client: anthropic.Anthropic,
    business: dict[str, Any],
    analysis: dict[str, Any],
    reviews: list[dict[str, Any]],
    *,
    model: str = LANDING_MODEL,
    output_dir: Path = CLIENTS_DIR,
) -> dict[str, Any]:
    """Genera la landing y la guarda. Devuelve metadata."""
    payload = build_payload(business, analysis, reviews)
    user_msg = build_user_message(payload)

    t0 = time.monotonic()

    # Streaming porque max_tokens es grande (16k)
    with client.messages.stream(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_msg}],
    ) as stream:
        final = stream.get_final_message()

    duration_s = time.monotonic() - t0

    # Concatena los bloques de tipo "text" (ignorando thinking blocks)
    html = "".join(
        b.text for b in final.content if getattr(b, "type", None) == "text"
    )
    html = _strip_html_fences(html)
    if not html.startswith("<!DOCTYPE") and "<html" not in html[:200]:
        raise RuntimeError(
            f"La respuesta no parece HTML valido. Primeros 200 chars: {html[:200]}"
        )

    slug = slugify(business["name"])
    output_dir.mkdir(parents=True, exist_ok=True)
    landing_dir = output_dir / slug
    landing_dir.mkdir(exist_ok=True)
    html_path = landing_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    # Metadata para debug y la galeria
    usage = {
        "input_tokens": final.usage.input_tokens,
        "output_tokens": final.usage.output_tokens,
        "cache_read_input_tokens": final.usage.cache_read_input_tokens or 0,
        "cache_creation_input_tokens": final.usage.cache_creation_input_tokens or 0,
    }
    cost_usd = _calc_cost(usage, model)

    meta = {
        "place_id": business["place_id"],
        "business_name": business["name"],
        "category": business["category"],
        "zone": business["zone"],
        "rating": business.get("rating"),
        "reviews_count": business.get("reviews_count"),
        "phone": business.get("phone"),
        "address": business.get("address"),
        "website_kind": business.get("website_kind"),
        "model": model,
        "usage": usage,
        "cost_usd": round(cost_usd, 4),
        "duration_s": round(duration_s, 1),
        "html_path": str(html_path.relative_to(output_dir.parent)),
        "slug": slug,
    }
    (landing_dir / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Guardar prompt para debug/iteracion futura
    (landing_dir / "_prompt.txt").write_text(user_msg, encoding="utf-8")

    return meta


def build_batch(count: int, *, model: str = LANDING_MODEL) -> dict[str, Any]:
    """Genera N landings con seleccion balanceada por categoria."""
    db.init_db()
    client = get_client()

    with db.connect() as conn:
        targets = db.select_balanced_leads(conn, count)

    if not targets:
        console.log("[yellow]No hay leads disponibles (analizados, sin web, no descartados).")
        return {"generated": 0, "errors": 0, "total_cost": 0.0}

    console.log(
        f"[bold]Generando {len(targets)} landings con {model}[/bold]\n"
        + "\n".join(
            f"  • {r['name']} ({r['category']}/{r['zone']}, {r['reviews_count']}★)"
            for r in targets
        )
    )

    generated = 0
    errors = 0
    total_cost = 0.0
    metas: list[dict[str, Any]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        console=console,
    ) as progress:
        for row in targets:
            biz = dict(row)
            with db.connect() as conn:
                ana = db.get_analysis(conn, biz["place_id"])
                if not ana:
                    console.log(f"[yellow]skip {biz['name']}: sin analisis")
                    continue
                analysis = dict(ana)
                top_reviews = [dict(r) for r in db.get_top_reviews_for_landing(conn, biz["place_id"])]

            task = progress.add_task(
                f"Construyendo {biz['name'][:40]}", total=None
            )
            try:
                meta = generate_landing(
                    client, biz, analysis, top_reviews, model=model
                )
            except Exception as e:  # pragma: no cover
                console.log(f"[red]error {biz['name']}: {e}")
                errors += 1
                progress.remove_task(task)
                continue

            with db.connect() as conn:
                db.save_landing(
                    conn,
                    biz["place_id"],
                    output_path=meta["html_path"],
                    slug=meta["slug"],
                    model=model,
                    usage=meta["usage"],
                    cost_usd=meta["cost_usd"],
                    duration_s=meta["duration_s"],
                )

            metas.append(meta)
            generated += 1
            total_cost += meta["cost_usd"]
            console.log(
                f"[green]✓ {biz['name'][:50]}  →  clients/{meta['slug']}/  "
                f"(${meta['cost_usd']:.3f}, {meta['duration_s']:.0f}s)"
            )
            progress.remove_task(task)

    # Generar la galeria
    build_gallery()

    console.log(
        f"\n[bold green]Generadas {generated}/{len(targets)} landings[/bold green]  "
        f"costo total ~USD {total_cost:.2f}"
    )
    return {
        "generated": generated,
        "errors": errors,
        "total_cost": round(total_cost, 2),
        "metas": metas,
    }


def build_gallery() -> Path:
    """Regenera clients/index.html como portfolio publico (deployable a Vercel)."""
    CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT
                l.slug,
                b.name, b.category, b.zone, b.rating, b.reviews_count,
                b.website_kind,
                a.angles, a.summary
            FROM landings l
            JOIN businesses b ON b.place_id = l.place_id
            LEFT JOIN analysis a ON a.place_id = l.place_id
            ORDER BY b.reviews_count DESC NULLS LAST
            """
        ).fetchall()

    cards_html = "\n".join(_render_card(dict(r), idx + 1) for idx, r in enumerate(rows))
    categories = sorted({r["category"] for r in rows})
    zones = sorted({r["zone"] for r in rows})

    html = _GALLERY_TEMPLATE.format(
        brand=LANDING_BRAND,
        total=len(rows),
        categories_count=len(categories),
        zones_count=len(zones),
        cards=cards_html,
    )

    out = CLIENTS_DIR / "index.html"
    out.write_text(html, encoding="utf-8")

    # Crear .vercelignore para que metadata interna no suba a prod
    vercelignore = CLIENTS_DIR / ".vercelignore"
    vercelignore.write_text(
        "# Excluir metadata interna de cada landing del deploy\n"
        "**/_meta.json\n"
        "**/_prompt.txt\n",
        encoding="utf-8",
    )

    console.log(f"[blue]Portfolio regenerado: {out}")
    return out


def _extract_pitch(angles_raw: Any) -> str:
    """Toma el primer angulo del analisis y lo limpia (saca comillas, etc.)."""
    if not angles_raw:
        return ""
    angles = json.loads(angles_raw) if isinstance(angles_raw, str) else angles_raw
    if not angles:
        return ""
    pitch = str(angles[0])
    # Sacar comillas envolvientes y notas explicativas tipo " — para mamas..."
    pitch = pitch.strip(" \"'“”‘’")
    if " — " in pitch:
        pitch = pitch.split(" — ")[0].strip(" \"'“”‘’")
    if "—" in pitch and len(pitch.split("—")[0]) > 30:
        pitch = pitch.split("—")[0].strip(" \"'“”‘’")
    return pitch


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_card(row: dict[str, Any], idx: int) -> str:
    name = _escape(row.get("name", ""))
    category = _escape(row.get("category", ""))
    zone = _escape(row.get("zone", "").replace("_", " "))
    rating = row.get("rating") or 0.0
    reviews = row.get("reviews_count") or 0
    pitch = _escape(_extract_pitch(row.get("angles")))
    slug = row["slug"]

    return f"""
    <a href="{slug}/index.html" class="case">
      <div class="case-head">
        <span class="case-num">{idx:02d}</span>
        <span class="case-tag">{category} · {zone}</span>
      </div>
      <h3 class="case-name">{name}</h3>
      <p class="case-pitch">"{pitch}"</p>
      <div class="case-foot">
        <span class="case-stat">★ {rating:.1f} · {reviews} reseñas</span>
        <span class="case-cta">ver →</span>
      </div>
    </a>
    """


_GALLERY_TEMPLATE = """<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rodrigo Domínguez · Software Engineer</title>
<meta name="description" content="Software Engineer con 10+ años de experiencia. Despegar, Mercado Libre, Kavak, Ualá. Ahora construyendo landings para comercios de San Luis.">
<meta name="theme-color" content="#1c1815">
<link rel="icon" href="data:image/svg+xml,&lt;svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22&gt;&lt;text x=%220%22 y=%2214%22 font-size=%2214%22 font-family=%22monospace%22 fill=%22%23b84a1f%22&gt;▸&lt;/text&gt;&lt;/svg&gt;">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rubik:ital,wght@0,300;0,400;0,500;0,600;0,700;0,800;0,900;1,300;1,400;1,500;1,600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0e0d0c;
    --bg-alt: #161413;
    --ink: #f5f0e6;
    --ink-soft: #c9c2b6;
    --muted: #7a7468;
    --line: #2a2622;
    --line-soft: #1d1a17;
    --rust: #d65a2a;
    --rust-soft: #b84a1f;
    --green: #6a9968;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ background: var(--bg); color: var(--ink); }}
  body {{
    font-family: 'Rubik', system-ui, sans-serif;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }}
  .container {{ max-width: 1320px; margin: 0 auto; padding: 0 32px; }}
  @media (max-width: 640px) {{ .container {{ padding: 0 20px; }} }}
  .mono {{ font-family: 'JetBrains Mono', ui-monospace, monospace; }}
  a {{ color: inherit; text-decoration: none; }}

  /* === TOP BAR === */
  .topbar {{
    border-bottom: 1px solid var(--line);
    padding: 14px 0;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--ink-soft);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }}
  .topbar .brand-mark {{ display: flex; align-items: center; gap: 10px; }}
  .topbar .brand-dot {{
    width: 8px; height: 8px; border-radius: 9999px;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2.4s ease-in-out infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.5; }}
  }}
  .topbar .brand-name {{ color: var(--ink); font-weight: 600; letter-spacing: 0.1em; }}

  .contact-links {{
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0;
  }}
  .contact-links a {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-soft);
    padding: 6px 14px;
    border-left: 1px solid var(--line);
    transition: color 0.2s ease, background 0.2s ease;
    font-weight: 500;
  }}
  .contact-links a:first-child {{ border-left: none; padding-left: 0; }}
  .contact-links a:hover {{ color: var(--rust); }}
  .contact-links .ext {{
    margin-left: 4px;
    opacity: 0.5;
    transition: opacity 0.2s ease, color 0.2s ease;
  }}
  .contact-links a:hover .ext {{ opacity: 1; color: var(--rust); }}
  @media (max-width: 640px) {{
    .contact-links a {{ padding: 6px 10px; font-size: 10px; }}
    .contact-links a:first-child {{ padding-left: 0; }}
  }}

  /* === HERO === */
  header.hero {{
    padding: 96px 0 80px;
    border-bottom: 1px solid var(--line);
    position: relative;
  }}
  .hero-eyebrow {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.2em;
    color: var(--rust);
    font-weight: 600;
    margin-bottom: 28px;
    text-transform: uppercase;
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .hero-eyebrow::before {{
    content: '▸';
    color: var(--rust);
    font-weight: 700;
  }}
  .hero-eyebrow .arrow-line {{
    flex: 1;
    height: 1px;
    background: linear-gradient(90deg, var(--rust) 0%, transparent 70%);
    max-width: 200px;
    opacity: 0.4;
  }}

  h1.name {{
    font-family: 'Rubik', sans-serif;
    font-weight: 700;
    font-size: clamp(56px, 12vw, 172px);
    line-height: 0.9;
    letter-spacing: -0.045em;
    margin-left: -0.03em;
    margin-bottom: 56px;
    color: var(--ink);
  }}
  h1.name em {{
    font-style: normal;
    font-weight: 300;
    color: var(--ink-soft);
  }}
  h1.name .dot {{ color: var(--rust); font-weight: 700; }}

  .lead {{
    display: grid;
    grid-template-columns: 1fr;
    gap: 40px;
    max-width: 1100px;
  }}
  @media (min-width: 900px) {{
    .lead {{ grid-template-columns: 7fr 5fr; gap: 80px; align-items: start; }}
  }}
  .lead-text {{
    font-family: 'Rubik', sans-serif;
    font-size: clamp(20px, 2.3vw, 28px);
    font-weight: 300;
    line-height: 1.32;
    letter-spacing: -0.012em;
    color: var(--ink);
  }}
  .lead-text em {{ font-style: italic; color: var(--rust); font-weight: 400; }}
  .lead-text .accent {{ color: var(--rust); }}

  .stack-block {{
    border-top: 1px solid var(--line);
    padding-top: 24px;
  }}
  .stack-label {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 18px;
  }}
  .stack-list {{
    display: flex;
    flex-wrap: wrap;
    gap: 10px 14px;
    align-items: center;
  }}
  .stack-item {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 500;
    color: var(--ink);
    padding: 6px 12px;
    border: 1px solid var(--line);
    border-radius: 2px;
    background: var(--bg-alt);
    letter-spacing: 0.04em;
  }}
  .stack-item:hover {{ border-color: var(--rust); color: var(--rust); }}

  /* === STATS BAND === */
  .stats {{
    border-bottom: 1px solid var(--line);
    background: var(--bg-alt);
  }}
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0;
  }}
  @media (min-width: 768px) {{ .stats-grid {{ grid-template-columns: repeat(4, 1fr); }} }}
  .stat {{
    padding: 28px 32px;
    border-right: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
  }}
  @media (min-width: 768px) {{
    .stat:nth-child(4) {{ border-right: none; }}
    .stat {{ border-bottom: none; }}
  }}
  @media (max-width: 767px) {{
    .stat:nth-child(2n) {{ border-right: none; }}
    .stat:nth-child(n+3) {{ border-bottom: none; }}
  }}
  .stat-label {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 10px;
  }}
  .stat-value {{
    font-family: 'Rubik', sans-serif;
    font-size: clamp(36px, 5vw, 56px);
    font-weight: 600;
    line-height: 1;
    letter-spacing: -0.02em;
    color: var(--ink);
  }}
  .stat-value .unit {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    color: var(--muted);
    margin-left: 6px;
    font-weight: 500;
    letter-spacing: 0.12em;
  }}

  /* === PORTFOLIO === */
  section.portfolio {{ padding: 96px 0 120px; }}
  .section-head {{
    display: flex;
    justify-content: space-between;
    align-items: end;
    flex-wrap: wrap;
    gap: 32px;
    margin-bottom: 24px;
  }}
  .section-tag {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--rust);
    font-weight: 600;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    gap: 12px;
  }}
  .section-tag::before {{ content: '//'; color: var(--rust); opacity: 0.6; }}
  .section-title {{
    font-family: 'Rubik', sans-serif;
    font-size: clamp(40px, 5.5vw, 72px);
    font-weight: 600;
    line-height: 0.95;
    letter-spacing: -0.028em;
    max-width: 800px;
  }}
  .section-title em {{ font-style: italic; font-weight: 400; }}
  .section-title .accent {{ color: var(--rust); }}
  .section-counter {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--muted);
    text-align: right;
    white-space: nowrap;
  }}
  .section-counter strong {{
    color: var(--ink);
    font-weight: 600;
    font-size: 13px;
  }}
  .section-intro {{
    font-family: 'Rubik', sans-serif;
    font-style: italic;
    font-size: clamp(16px, 1.6vw, 20px);
    color: var(--ink-soft);
    max-width: 760px;
    margin: 24px 0 56px;
    line-height: 1.55;
  }}

  /* === CASE CARDS === */
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 1px;
    background: var(--line);
    border: 1px solid var(--line);
  }}
  .case {{
    background: var(--bg);
    padding: 32px 28px 24px;
    display: flex;
    flex-direction: column;
    gap: 18px;
    transition: background 0.25s ease;
    min-height: 280px;
    position: relative;
  }}
  .case:hover {{ background: var(--bg-alt); }}
  .case:hover .case-cta {{ color: var(--rust); }}
  .case:hover .case-name {{ color: var(--rust); }}

  .case-head {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
  }}
  .case-num {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 600;
    color: var(--rust);
    letter-spacing: 0.08em;
  }}
  .case-tag {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--muted);
    text-align: right;
  }}
  .case-name {{
    font-family: 'Rubik', sans-serif;
    font-size: 26px;
    font-weight: 600;
    line-height: 1.1;
    letter-spacing: -0.015em;
    color: var(--ink);
    transition: color 0.2s ease;
  }}
  .case-pitch {{
    font-family: 'Rubik', sans-serif;
    font-style: italic;
    font-size: 17px;
    line-height: 1.35;
    color: var(--ink-soft);
    flex: 1;
    /* limpio comillas si el contenido viene con ellas */
  }}
  .case-foot {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-top: 16px;
    border-top: 1px solid var(--line);
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.1em;
  }}
  .case-stat {{
    color: var(--muted);
    font-weight: 500;
  }}
  .case-cta {{
    color: var(--ink-soft);
    text-transform: uppercase;
    letter-spacing: 0.18em;
    font-weight: 600;
    transition: color 0.2s ease;
  }}

  /* === ABOUT BAND === */
  section.about {{
    border-top: 1px solid var(--line);
    background: var(--bg-alt);
    padding: 80px 0;
  }}
  .about-grid {{
    display: grid;
    grid-template-columns: 1fr;
    gap: 32px;
    max-width: 1100px;
  }}
  @media (min-width: 768px) {{
    .about-grid {{ grid-template-columns: 1fr 2fr; gap: 64px; }}
  }}
  .about-tag {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: var(--rust);
    font-weight: 600;
  }}
  .about-tag::before {{ content: '// '; opacity: 0.6; }}
  .about-text {{
    font-family: 'Rubik', sans-serif;
    font-size: clamp(18px, 2vw, 24px);
    font-weight: 300;
    line-height: 1.45;
    color: var(--ink);
    letter-spacing: -0.01em;
  }}
  .about-text em {{ font-style: italic; color: var(--rust); }}
  .about-text p + p {{ margin-top: 20px; }}

  /* === FOOTER === */
  footer.site-footer {{
    border-top: 1px solid var(--line);
    padding: 32px 0 40px;
  }}
  .footer-grid {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 20px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.12em;
    color: var(--muted);
  }}
  .footer-grid .brand {{
    color: var(--ink);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
  }}
  .footer-grid .copy-note {{ opacity: 0.7; }}
  .footer-links {{
    display: flex;
    gap: 0;
    align-items: center;
  }}
  .footer-links a {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--ink-soft);
    padding: 4px 14px;
    border-left: 1px solid var(--line);
    transition: color 0.2s ease;
    font-weight: 500;
  }}
  .footer-links a:first-child {{ border-left: none; padding-left: 0; }}
  .footer-links a:hover {{ color: var(--rust); }}

  ::selection {{ background: var(--rust); color: var(--ink); }}
  ::-moz-selection {{ background: var(--rust); color: var(--ink); }}

  /* link focus visible */
  a:focus-visible {{ outline: 2px solid var(--rust); outline-offset: 4px; }}
</style>
</head>
<body>
  <div class="container">

    <div class="topbar">
      <div class="brand-mark">
        <span class="brand-dot"></span>
        <span class="brand-name">{brand}</span>
      </div>
      <nav class="contact-links">
        <a href="https://www.linkedin.com/in/rodrigo-martin-dominguez-463b5a33/" target="_blank" rel="noopener noreferrer">LinkedIn<span class="ext">↗</span></a>
        <a href="mailto:rodrigomartind@gmail.com">Email<span class="ext">↗</span></a>
        <a href="https://wa.me/5491134000444?text=Hola%20Rodrigo%21%20Vi%20tu%20portfolio%20y%20quer%C3%ADa%20charlar." target="_blank" rel="noopener noreferrer">WhatsApp<span class="ext">↗</span></a>
      </nav>
    </div>

    <header class="hero">
      <div class="hero-eyebrow">
        <span>Software Engineer · Buenos Aires</span>
        <span class="arrow-line"></span>
      </div>

      <h1 class="name">Rodrigo<br><em>Domínguez</em><span class="dot">.</span></h1>

      <div class="lead">
        <p class="lead-text">
          Diseño y construyo <em>productos digitales</em> desde hace más de <em>10 años</em>. Esta es mi <span class="accent">software factory</span> personal — donde uso lo que aprendí en escala para construir landings que <em>convierten</em> para comercios de San Luis.
        </p>
        <div class="stack-block">
          <div class="stack-label">// pasé por</div>
          <div class="stack-list">
            <span class="stack-item">Despegar</span>
            <span class="stack-item">Mercado Libre</span>
            <span class="stack-item">Kavak</span>
            <span class="stack-item">Ualá</span>
          </div>
        </div>
      </div>
    </header>

  </div>

  <section class="stats">
    <div class="container">
      <div class="stats-grid">
        <div class="stat">
          <div class="stat-label">// proyectos</div>
          <div class="stat-value">{total}<span class="unit">activos</span></div>
        </div>
        <div class="stat">
          <div class="stat-label">// rubros</div>
          <div class="stat-value">{categories_count}<span class="unit">verticales</span></div>
        </div>
        <div class="stat">
          <div class="stat-label">// ciudades</div>
          <div class="stat-value">{zones_count}<span class="unit">san luis</span></div>
        </div>
        <div class="stat">
          <div class="stat-label">// experiencia</div>
          <div class="stat-value">10<span class="unit">años</span></div>
        </div>
      </div>
    </div>
  </section>

  <div class="container">

    <section class="portfolio">
      <div class="section-head">
        <div>
          <div class="section-tag">case studies · 2026</div>
          <h2 class="section-title">Landings <em>en producción</em><span class="accent">.</span></h2>
        </div>
        <div class="section-counter">
          <strong>{total}</strong> proyectos<br>
          <span style="opacity: 0.6;">listos para presentar</span>
        </div>
      </div>
      <p class="section-intro">
        Cada proyecto se construye sobre lo que las reseñas reales del comercio dicen — sus fortalezas, sus dolores, los ángulos que solo alguien que las leyó atentamente sabría usar. La cita en cada card es el ángulo principal de la landing.
      </p>
      <div class="grid">
        {cards}
      </div>
    </section>

    <section class="about">
      <div class="about-grid" style="margin: 0 -32px; padding: 0 32px;">
        <div>
          <div class="about-tag">cómo trabajo</div>
        </div>
        <div class="about-text">
          <p>
            Junto reseñas de Google, las analizo, extraigo los <em>ángulos de venta reales</em> — y los convierto en landings que se sienten escritas por alguien que conoce el negocio.
          </p>
          <p>
            Sin templates. Sin AI slop. Cada landing tiene su propia tipografía, su propia paleta, su propio ritmo — anclados en lo que dice la gente que ya es cliente.
          </p>
        </div>
      </div>
    </section>

    <footer class="site-footer">
      <div class="footer-grid">
        <span class="brand">{brand}</span>
        <div class="footer-links">
          <a href="https://www.linkedin.com/in/rodrigo-martin-dominguez-463b5a33/" target="_blank" rel="noopener noreferrer">LinkedIn</a>
          <a href="mailto:rodrigomartind@gmail.com">Email</a>
          <a href="https://wa.me/5491134000444?text=Hola%20Rodrigo%21%20Vi%20tu%20portfolio%20y%20quer%C3%ADa%20charlar." target="_blank" rel="noopener noreferrer">WhatsApp</a>
        </div>
        <span class="copy-note">2026 · San Luis · Argentina</span>
      </div>
    </footer>

  </div>
</body>
</html>
"""
