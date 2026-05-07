"""SQLite schema y helpers."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    place_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    google_types    TEXT,
    zone            TEXT NOT NULL,
    address         TEXT,
    lat             REAL,
    lng             REAL,
    phone           TEXT,
    website         TEXT,
    website_kind    TEXT,  -- none, instagram, facebook, tiktok, linktree, whatsapp, directory, real
    rating          REAL,
    reviews_count   INTEGER,
    price_level     INTEGER,
    business_status TEXT,
    hours           TEXT,
    discovered_at   TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_zone     ON businesses(zone);
CREATE INDEX IF NOT EXISTS idx_biz_category ON businesses(category);
CREATE INDEX IF NOT EXISTS idx_biz_website  ON businesses(website);

CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id    TEXT NOT NULL,
    author      TEXT,
    rating      INTEGER,
    text        TEXT,
    language    TEXT,
    posted_at   TEXT,
    fetched_at  TEXT NOT NULL,
    UNIQUE(place_id, author, posted_at, text),
    FOREIGN KEY (place_id) REFERENCES businesses(place_id)
);

CREATE INDEX IF NOT EXISTS idx_reviews_place ON reviews(place_id);

CREATE TABLE IF NOT EXISTS analysis (
    place_id          TEXT PRIMARY KEY,
    summary           TEXT,
    strengths         TEXT,
    pains             TEXT,
    angles            TEXT,
    tone              TEXT,
    target_audience   TEXT,
    keywords          TEXT,
    reviews_analyzed  INTEGER,
    model             TEXT,
    analyzed_at       TEXT NOT NULL,
    FOREIGN KEY (place_id) REFERENCES businesses(place_id)
);

CREATE TABLE IF NOT EXISTS pipeline_status (
    place_id          TEXT PRIMARY KEY,
    has_website       INTEGER NOT NULL DEFAULT 0,
    reviews_scraped   INTEGER NOT NULL DEFAULT 0,
    analyzed          INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'pending',
    notes             TEXT,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (place_id) REFERENCES businesses(place_id)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_status ON pipeline_status(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_no_web ON pipeline_status(has_website);

CREATE TABLE IF NOT EXISTS landings (
    place_id        TEXT PRIMARY KEY,
    output_path     TEXT NOT NULL,
    slug            TEXT NOT NULL,
    model           TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cache_read      INTEGER,
    cache_create    INTEGER,
    cost_usd        REAL,
    duration_s      REAL,
    generated_at    TEXT NOT NULL,
    FOREIGN KEY (place_id) REFERENCES businesses(place_id)
);
CREATE INDEX IF NOT EXISTS idx_landings_at ON landings(generated_at);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Patrones para clasificar el campo "website" de Places API.
# Un negocio puede tener Instagram cargado como "web" — eso es un LEAD igual,
# no un negocio con landing real. La idea es no descartar esos casos.
_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("whatsapp", ("wa.me", "api.whatsapp.com", "chat.whatsapp.com")),
    ("instagram", ("instagram.com", "instagr.am")),
    ("facebook", ("facebook.com", "fb.com", "fb.me", "m.facebook.com")),
    ("tiktok", ("tiktok.com",)),
    ("linktree", ("linktr.ee", "beacons.ai", "lnk.bio", "taplink.cc", "campsite.bio", "bio.link")),
    ("directory", (
        "paginasamarillas",
        "ofreceronline",
        "guiadelasalud",
        "guiaclarin",
        "yelp.com",
        "tripadvisor",
        "booking.com",
        "despegar.com",
    )),
]


def classify_website(url: str | None) -> str:
    """Clasifica el campo website. Solo 'real' significa que tiene landing propia."""
    if not url:
        return "none"
    u = url.lower()
    for kind, patterns in _PATTERNS:
        if any(p in u for p in patterns):
            return kind
    return "real"


def _migrate(conn: sqlite3.Connection) -> None:
    """Migraciones idempotentes para DBs creadas antes de algun cambio de schema."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(businesses)").fetchall()}
    if "website_kind" not in cols:
        conn.execute("ALTER TABLE businesses ADD COLUMN website_kind TEXT")


def backfill_website_kind() -> dict[str, int]:
    """Aplica classify_website() retroactivamente sobre toda la base.

    Tambien recalcula has_website en pipeline_status:
    has_website = 1 SOLO si website_kind == 'real'.
    """
    counts: dict[str, int] = {}
    with connect() as conn:
        rows = conn.execute("SELECT place_id, website FROM businesses").fetchall()
        for r in rows:
            kind = classify_website(r["website"])
            counts[kind] = counts.get(kind, 0) + 1
            conn.execute(
                "UPDATE businesses SET website_kind = ? WHERE place_id = ?",
                (kind, r["place_id"]),
            )
        # Recalcular has_website: solo 'real' cuenta como tener web propia.
        conn.execute(
            """
            UPDATE pipeline_status
            SET has_website = CASE
                WHEN (
                    SELECT website_kind FROM businesses
                    WHERE businesses.place_id = pipeline_status.place_id
                ) = 'real' THEN 1
                ELSE 0
            END,
            updated_at = ?
            """,
            (now_iso(),),
        )
    return counts


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def upsert_business(conn: sqlite3.Connection, biz: dict[str, Any]) -> None:
    """Insert o update segun place_id. Tambien upsertea pipeline_status."""
    biz = dict(biz)
    biz.setdefault("discovered_at", now_iso())
    biz["updated_at"] = now_iso()
    biz["website_kind"] = classify_website(biz.get("website"))
    if isinstance(biz.get("google_types"), list):
        biz["google_types"] = json.dumps(biz["google_types"])
    if isinstance(biz.get("hours"), (list, dict)):
        biz["hours"] = json.dumps(biz["hours"])

    conn.execute(
        """
        INSERT INTO businesses (
            place_id, name, category, google_types, zone, address, lat, lng,
            phone, website, website_kind, rating, reviews_count, price_level,
            business_status, hours, discovered_at, updated_at
        ) VALUES (
            :place_id, :name, :category, :google_types, :zone, :address, :lat, :lng,
            :phone, :website, :website_kind, :rating, :reviews_count, :price_level,
            :business_status, :hours, :discovered_at, :updated_at
        )
        ON CONFLICT(place_id) DO UPDATE SET
            name           = excluded.name,
            category       = excluded.category,
            google_types   = excluded.google_types,
            address        = excluded.address,
            phone          = COALESCE(excluded.phone, businesses.phone),
            website        = COALESCE(excluded.website, businesses.website),
            website_kind   = excluded.website_kind,
            rating         = excluded.rating,
            reviews_count  = excluded.reviews_count,
            price_level    = excluded.price_level,
            business_status= excluded.business_status,
            hours          = COALESCE(excluded.hours, businesses.hours),
            updated_at     = excluded.updated_at
        """,
        biz,
    )

    # has_website = 1 SOLO si tiene web propia (real). Insta/FB/etc -> sigue siendo lead.
    has_real_web = 1 if biz["website_kind"] == "real" else 0
    conn.execute(
        """
        INSERT INTO pipeline_status (place_id, has_website, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(place_id) DO UPDATE SET
            has_website = excluded.has_website,
            updated_at  = excluded.updated_at
        """,
        (biz["place_id"], has_real_web, now_iso()),
    )


def insert_reviews(
    conn: sqlite3.Connection,
    place_id: str,
    reviews: list[dict[str, Any]],
    mark_scraped: bool = True,
) -> int:
    """Inserta reviews; con mark_scraped=False no toca pipeline_status (uso desde
    discover, para que Outscraper igual pueda enriquecer despues)."""
    inserted = 0
    for r in reviews:
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO reviews (
                    place_id, author, rating, text, language, posted_at, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    place_id,
                    r.get("author"),
                    r.get("rating"),
                    r.get("text"),
                    r.get("language"),
                    r.get("posted_at"),
                    now_iso(),
                ),
            )
            inserted += conn.total_changes and 1 or 0
        except sqlite3.IntegrityError:
            pass
    if mark_scraped:
        conn.execute(
            """
            UPDATE pipeline_status SET reviews_scraped = 1, updated_at = ?
            WHERE place_id = ?
            """,
            (now_iso(), place_id),
        )
    return inserted


def save_analysis(conn: sqlite3.Connection, place_id: str, payload: dict[str, Any], model: str, n_reviews: int) -> None:
    def to_json(v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            return v
        return json.dumps(v, ensure_ascii=False)

    conn.execute(
        """
        INSERT INTO analysis (
            place_id, summary, strengths, pains, angles, tone,
            target_audience, keywords, reviews_analyzed, model, analyzed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(place_id) DO UPDATE SET
            summary          = excluded.summary,
            strengths        = excluded.strengths,
            pains            = excluded.pains,
            angles           = excluded.angles,
            tone             = excluded.tone,
            target_audience  = excluded.target_audience,
            keywords         = excluded.keywords,
            reviews_analyzed = excluded.reviews_analyzed,
            model            = excluded.model,
            analyzed_at      = excluded.analyzed_at
        """,
        (
            place_id,
            payload.get("summary"),
            to_json(payload.get("strengths")),
            to_json(payload.get("pains")),
            to_json(payload.get("angles")),
            payload.get("tone"),
            payload.get("target_audience"),
            to_json(payload.get("keywords")),
            n_reviews,
            model,
            now_iso(),
        ),
    )
    conn.execute(
        """
        UPDATE pipeline_status SET analyzed = 1, updated_at = ?
        WHERE place_id = ?
        """,
        (now_iso(), place_id),
    )


def fetch_no_website(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = """
        SELECT b.*
        FROM businesses b
        JOIN pipeline_status p ON p.place_id = b.place_id
        WHERE p.has_website = 0
          AND (b.business_status IS NULL OR b.business_status = 'OPERATIONAL')
        ORDER BY b.reviews_count DESC NULLS LAST
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def fetch_for_review_scrape(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT b.*
        FROM businesses b
        JOIN pipeline_status p ON p.place_id = b.place_id
        WHERE p.has_website = 0
          AND p.reviews_scraped = 0
          AND b.reviews_count > 0
        ORDER BY b.reviews_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def fetch_for_analysis(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT b.*
        FROM businesses b
        JOIN pipeline_status p ON p.place_id = b.place_id
        WHERE p.has_website = 0
          AND p.analyzed = 0
        ORDER BY b.reviews_count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def fetch_reviews(conn: sqlite3.Connection, place_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reviews WHERE place_id = ? ORDER BY posted_at DESC",
        (place_id,),
    ).fetchall()


def save_landing(
    conn: sqlite3.Connection,
    place_id: str,
    *,
    output_path: str,
    slug: str,
    model: str,
    usage: dict[str, int],
    cost_usd: float,
    duration_s: float,
) -> None:
    conn.execute(
        """
        INSERT INTO landings (
            place_id, output_path, slug, model, input_tokens, output_tokens,
            cache_read, cache_create, cost_usd, duration_s, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(place_id) DO UPDATE SET
            output_path  = excluded.output_path,
            slug         = excluded.slug,
            model        = excluded.model,
            input_tokens = excluded.input_tokens,
            output_tokens= excluded.output_tokens,
            cache_read   = excluded.cache_read,
            cache_create = excluded.cache_create,
            cost_usd     = excluded.cost_usd,
            duration_s   = excluded.duration_s,
            generated_at = excluded.generated_at
        """,
        (
            place_id,
            output_path,
            slug,
            model,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            usage.get("cache_read_input_tokens", 0),
            usage.get("cache_creation_input_tokens", 0),
            cost_usd,
            duration_s,
            now_iso(),
        ),
    )
    conn.execute(
        """
        UPDATE pipeline_status SET status = 'landing_hecha', updated_at = ?
        WHERE place_id = ?
        """,
        (now_iso(), place_id),
    )


def select_balanced_leads(
    conn: sqlite3.Connection,
    target_count: int,
    *,
    min_reviews: int = 30,
    min_rating: float = 4.0,
) -> list[sqlite3.Row]:
    """Selecciona N leads balanceando categorias.

    Ranking: dentro de cada categoria por reviews_count desc.
    Iteracion: round-robin entre categorias hasta alcanzar target.
    """
    rows = conn.execute(
        """
        SELECT b.place_id, b.name, b.category, b.zone, b.address, b.lat, b.lng,
               b.phone, b.website, b.website_kind, b.rating, b.reviews_count,
               b.hours,
               ROW_NUMBER() OVER (
                   PARTITION BY b.category
                   ORDER BY b.reviews_count DESC, b.rating DESC
               ) as rn
        FROM businesses b
        JOIN pipeline_status p ON p.place_id = b.place_id
        JOIN analysis a ON a.place_id = b.place_id
        LEFT JOIN landings l ON l.place_id = b.place_id
        WHERE p.has_website = 0
          AND p.analyzed = 1
          AND p.status NOT IN ('landing_hecha', 'descartado')
          AND b.reviews_count >= ?
          AND b.rating >= ?
          AND l.place_id IS NULL
        ORDER BY rn ASC, b.reviews_count DESC
        """,
        (min_reviews, min_rating),
    ).fetchall()

    # Round-robin: agrupar por categoria, ir tomando uno de cada hasta target
    by_cat: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    # Ordenar las listas por reviews_count desc (ya viene asi por SQL pero por las dudas)
    for cat in by_cat:
        by_cat[cat].sort(key=lambda r: -(r["reviews_count"] or 0))

    selected: list[sqlite3.Row] = []
    cats = sorted(by_cat.keys())
    while len(selected) < target_count and any(by_cat[c] for c in cats):
        for cat in cats:
            if len(selected) >= target_count:
                break
            if by_cat[cat]:
                selected.append(by_cat[cat].pop(0))
    return selected


def get_analysis(conn: sqlite3.Connection, place_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM analysis WHERE place_id = ?", (place_id,)
    ).fetchone()


def get_top_reviews_for_landing(
    conn: sqlite3.Connection, place_id: str, n: int = 6
) -> list[sqlite3.Row]:
    """Top N reviews para citar en la landing: priorizando rating alto y largas."""
    return conn.execute(
        """
        SELECT author, rating, text, language, posted_at
        FROM reviews
        WHERE place_id = ?
          AND text IS NOT NULL
          AND length(text) > 30
        ORDER BY rating DESC, length(text) DESC
        LIMIT ?
        """,
        (place_id, n),
    ).fetchall()


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    def count(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "businesses": count("SELECT COUNT(*) FROM businesses"),
        "no_website": count("SELECT COUNT(*) FROM pipeline_status WHERE has_website = 0"),
        "reviews_scraped": count(
            "SELECT COUNT(*) FROM pipeline_status WHERE reviews_scraped = 1"
        ),
        "analyzed": count("SELECT COUNT(*) FROM pipeline_status WHERE analyzed = 1"),
        "total_reviews": count("SELECT COUNT(*) FROM reviews"),
        "landings": count("SELECT COUNT(*) FROM landings"),
    }
