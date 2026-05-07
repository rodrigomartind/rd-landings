"""Analiza top N por categoria, en lugar del top global del CLI default.

Uso:
    .venv/bin/python scripts/analyze_top.py [N]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import analyze, db
from src.config import ANTHROPIC_MODEL

PER_CATEGORY = int(sys.argv[1]) if len(sys.argv) > 1 else 30


def main() -> None:
    db.init_db()
    client = analyze.get_client()

    with db.connect() as conn:
        rows = conn.execute(
            """
            WITH ranked AS (
                SELECT b.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY b.category
                           ORDER BY b.reviews_count DESC NULLS LAST
                       ) as rn
                FROM businesses b
                JOIN pipeline_status p ON p.place_id = b.place_id
                WHERE p.has_website = 0
                  AND p.analyzed = 0
                  AND b.reviews_count > 0
            )
            SELECT * FROM ranked WHERE rn <= ?
            ORDER BY category, reviews_count DESC
            """,
            (PER_CATEGORY,),
        ).fetchall()

    print(f"Candidatos: {len(rows)} (top {PER_CATEGORY} por categoria)")

    analyzed = 0
    skipped = 0
    errors = 0
    for r in rows:
        biz = dict(r)
        with db.connect() as conn:
            reviews = [dict(rv) for rv in db.fetch_reviews(conn, biz["place_id"])]
        usable = [rv for rv in reviews if (rv.get("text") or "").strip()]
        if len(usable) < 3:
            skipped += 1
            continue
        try:
            payload = analyze.analyze_business(client, biz, usable)
        except Exception as e:
            print(f"  error {biz['name'][:30]}: {e}")
            errors += 1
            continue
        with db.connect() as conn:
            db.save_analysis(
                conn, biz["place_id"], payload, ANTHROPIC_MODEL, len(usable)
            )
        analyzed += 1
        if analyzed % 10 == 0:
            print(f"  progreso: {analyzed} analizados / {skipped} skipped / {errors} errores")

    print(f"\nFINAL: {analyzed} analizados, {skipped} skipped (pocas reviews), {errors} errores")


if __name__ == "__main__":
    main()
