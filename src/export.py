"""Export a CSV, JSON o SQL para Supabase."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from rich.console import Console

from . import db
from .config import ROOT

console = Console()
EXPORT_DIR = ROOT / "data" / "exports"


SUPABASE_DDL = """
-- Schema para Supabase / Postgres
CREATE TABLE IF NOT EXISTS leads (
    place_id          text PRIMARY KEY,
    name              text NOT NULL,
    category          text NOT NULL,
    zone              text NOT NULL,
    address           text,
    lat               double precision,
    lng               double precision,
    phone             text,
    website           text,
    rating            real,
    reviews_count     integer,
    business_status   text,
    summary           text,
    strengths         jsonb,
    pains             jsonb,
    angles            jsonb,
    tone              text,
    target_audience   text,
    keywords          jsonb,
    status            text DEFAULT 'pending',
    discovered_at     timestamptz,
    analyzed_at       timestamptz
);

CREATE INDEX IF NOT EXISTS idx_leads_zone ON leads(zone);
CREATE INDEX IF NOT EXISTS idx_leads_category ON leads(category);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""


def _query_leads() -> list[dict[str, Any]]:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT b.place_id, b.name, b.category, b.zone, b.address, b.lat, b.lng,
                   b.phone, b.website, b.rating, b.reviews_count, b.business_status,
                   b.discovered_at,
                   a.summary, a.strengths, a.pains, a.angles, a.tone,
                   a.target_audience, a.keywords, a.analyzed_at,
                   p.status
            FROM businesses b
            JOIN pipeline_status p ON p.place_id = b.place_id
            LEFT JOIN analysis a ON a.place_id = b.place_id
            WHERE p.has_website = 0
            ORDER BY b.reviews_count DESC NULLS LAST
            """
        ).fetchall()
    return [dict(r) for r in rows]


def _to_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        console.log("[yellow]Nada para exportar")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            for k, v in list(r.items()):
                if isinstance(v, (list, dict)):
                    r[k] = json.dumps(v, ensure_ascii=False)
            writer.writerow(r)


def _to_json(rows: list[dict[str, Any]], path: Path) -> None:
    for r in rows:
        for fld in ["strengths", "pains", "angles", "keywords"]:
            v = r.get(fld)
            if isinstance(v, str):
                try:
                    r[fld] = json.loads(v)
                except json.JSONDecodeError:
                    pass
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _to_supabase_sql(rows: list[dict[str, Any]], path: Path) -> None:
    lines = [SUPABASE_DDL, "\n-- Inserts\n"]
    for r in rows:
        cols = [
            "place_id",
            "name",
            "category",
            "zone",
            "address",
            "lat",
            "lng",
            "phone",
            "website",
            "rating",
            "reviews_count",
            "business_status",
            "summary",
            "strengths",
            "pains",
            "angles",
            "tone",
            "target_audience",
            "keywords",
            "status",
            "discovered_at",
            "analyzed_at",
        ]
        values: list[str] = []
        for c in cols:
            v = r.get(c)
            if v is None:
                values.append("NULL")
            elif c in {"strengths", "pains", "angles", "keywords"}:
                if isinstance(v, str):
                    parsed = v
                else:
                    parsed = json.dumps(v, ensure_ascii=False)
                values.append(f"'{parsed.replace(chr(39), chr(39) * 2)}'::jsonb")
            elif isinstance(v, (int, float)):
                values.append(str(v))
            else:
                escaped = str(v).replace("'", "''")
                values.append(f"'{escaped}'")
        lines.append(
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join(values)}) "
            f"ON CONFLICT (place_id) DO NOTHING;"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def export(format: str = "csv", output: str | None = None) -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    rows = _query_leads()
    suffix = {"csv": "csv", "json": "json", "supabase-sql": "sql"}.get(format)
    if not suffix:
        raise ValueError(f"Formato no soportado: {format}")
    path = Path(output) if output else EXPORT_DIR / f"leads.{suffix}"

    if format == "csv":
        _to_csv(rows, path)
    elif format == "json":
        _to_json(rows, path)
    elif format == "supabase-sql":
        _to_supabase_sql(rows, path)

    console.log(f"[green]Exportados {len(rows)} leads a {path}")
    return path
