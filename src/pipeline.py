"""CLI orquestador del pipeline."""
from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import analyze, db, discover, export, landing, reviews
from .config import CATEGORIES, LANDING_MODEL, ZONES

app = typer.Typer(no_args_is_help=True, help="Pipeline de leads para San Luis")
console = Console()


@app.command()
def init() -> None:
    """Crea la base de datos vacia."""
    db.init_db()
    console.log(f"[green]DB lista en {db.DB_PATH}")


@app.command(name="discover")
def discover_cmd(
    zone: Optional[str] = typer.Option(None, help="Slug de zona (ej: san_luis)"),
    category: Optional[str] = typer.Option(None, help="Categoria (ej: peluqueria)"),
    all: bool = typer.Option(False, "--all", help="Todas las zonas y categorias"),
) -> None:
    """Barre Google Places y guarda negocios + filtra los que tienen web."""
    if all:
        discover.discover_all()
        return

    zones = [zone] if zone else None
    categories = [category] if category else None
    if not zones and not categories:
        console.log("[red]Pasa --zone, --category o --all")
        raise typer.Exit(1)
    discover.discover_all(zones=zones, categories=categories)


@app.command(name="scrape-reviews")
def scrape_reviews_cmd(
    limit: int = typer.Option(50, help="Cuantos negocios sin web procesar"),
) -> None:
    """Baja reviews completas via Outscraper para los negocios sin web."""
    reviews.scrape_reviews(limit=limit)


@app.command(name="analyze")
def analyze_cmd(
    limit: int = typer.Option(50, help="Cuantos negocios analizar"),
    min_reviews: int = typer.Option(3, help="Minimo de reviews con texto"),
) -> None:
    """Pasa los reviews por Claude y guarda el analisis estructurado."""
    analyze.analyze_pending(limit=limit, min_reviews=min_reviews)


@app.command(name="run-all")
def run_all(
    limit: int = typer.Option(30, help="Tope por etapa (scrape + analyze)"),
) -> None:
    """Pipeline completo end-to-end con la config actual."""
    discover.discover_all()
    reviews.scrape_reviews(limit=limit)
    analyze.analyze_pending(limit=limit)


@app.command()
def stats() -> None:
    """Resumen del estado de la base."""
    db.init_db()
    with db.connect() as conn:
        s = db.stats(conn)
    table = Table(title="Pipeline status")
    table.add_column("metric")
    table.add_column("count", justify="right")
    for k, v in s.items():
        table.add_row(k, str(v))
    console.print(table)


@app.command(name="list-leads")
def list_leads(
    limit: int = typer.Option(20),
    zone: Optional[str] = typer.Option(None),
) -> None:
    """Lista leads (negocios sin web, ordenados por reviews_count)."""
    db.init_db()
    with db.connect() as conn:
        sql = """
            SELECT b.name, b.category, b.zone, b.rating, b.reviews_count,
                   b.phone, b.address, p.analyzed
            FROM businesses b
            JOIN pipeline_status p ON p.place_id = b.place_id
            WHERE p.has_website = 0
        """
        params: list = []
        if zone:
            sql += " AND b.zone = ?"
            params.append(zone)
        sql += " ORDER BY b.reviews_count DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()

    table = Table(title=f"Top {limit} leads sin web")
    for col in ["name", "category", "zone", "rating", "reviews", "phone", "analizado"]:
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["name"][:40],
            r["category"],
            r["zone"],
            f"{r['rating']}" if r["rating"] is not None else "-",
            str(r["reviews_count"] or 0),
            r["phone"] or "-",
            "si" if r["analyzed"] else "no",
        )
    console.print(table)


@app.command()
def show(place_id: str) -> None:
    """Muestra detalle + analisis de un negocio."""
    db.init_db()
    with db.connect() as conn:
        biz = conn.execute(
            "SELECT * FROM businesses WHERE place_id = ?", (place_id,)
        ).fetchone()
        if not biz:
            console.log("[red]No encontrado")
            raise typer.Exit(1)
        ana = conn.execute(
            "SELECT * FROM analysis WHERE place_id = ?", (place_id,)
        ).fetchone()

    console.print(f"[bold]{biz['name']}[/bold]  ({biz['category']} / {biz['zone']})")
    console.print(f"  rating: {biz['rating']}  reviews: {biz['reviews_count']}")
    console.print(f"  direccion: {biz['address']}")
    console.print(f"  telefono: {biz['phone'] or '-'}")
    console.print(f"  web: {biz['website'] or '-'}")
    if ana:
        console.print("\n[bold yellow]Analisis:[/bold yellow]")
        console.print(f"  {ana['summary']}")
        for fld in ["strengths", "pains", "angles", "keywords"]:
            val = ana[fld]
            if val:
                items = json.loads(val) if isinstance(val, str) else val
                console.print(f"\n  [bold]{fld}:[/bold]")
                for it in items:
                    console.print(f"   - {it}")
        console.print(f"\n  tono: {ana['tone']}")
        console.print(f"  audiencia: {ana['target_audience']}")


@app.command(name="build-landings")
def build_landings_cmd(
    count: int = typer.Option(10, help="Cuantas landings generar"),
    model: str = typer.Option(LANDING_MODEL, help="claude-opus-4-7 | claude-sonnet-4-6"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo mostrar seleccion"),
) -> None:
    """Genera landings page para los top leads, balanceado por categoria."""
    if dry_run:
        db.init_db()
        with db.connect() as conn:
            picks = db.select_balanced_leads(conn, count)
        if not picks:
            console.log("[yellow]No hay candidatos.")
            return
        table = Table(title=f"Seleccion balanceada — {len(picks)} leads")
        for col in ["name", "category", "zone", "rating", "reviews", "phone"]:
            table.add_column(col)
        for r in picks:
            table.add_row(
                r["name"][:45],
                r["category"],
                r["zone"],
                f"{r['rating']}",
                str(r["reviews_count"] or 0),
                r["phone"] or "-",
            )
        console.print(table)
        return
    landing.build_batch(count, model=model)


@app.command(name="rebuild-gallery")
def rebuild_gallery() -> None:
    """Regenera clients/index.html sin generar landings nuevas."""
    landing.build_gallery()


@app.command(name="export")
def export_cmd(
    format: str = typer.Option("csv", help="csv | supabase-sql | json"),
    output: Optional[str] = typer.Option(None, help="Path de salida"),
) -> None:
    """Exporta leads a CSV, JSON o SQL para Supabase."""
    export.export(format=format, output=output)


@app.command(name="list-zones")
def list_zones() -> None:
    """Imprime las zonas configuradas."""
    for slug, z in ZONES.items():
        console.print(f"  {slug:18s} {z.label}")


@app.command(name="list-categories")
def list_categories() -> None:
    """Imprime las categorias que vamos a buscar."""
    for c in CATEGORIES:
        console.print(f"  {c}")


if __name__ == "__main__":
    app()
