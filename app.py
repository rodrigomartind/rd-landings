"""Dashboard local para revisar leads.db con socio.

Run:
    .venv/bin/streamlit run app.py
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pandas as pd
import streamlit as st

from src import db
from src.config import CATEGORIES, ZONES

st.set_page_config(
    page_title="Landing Leads — San Luis",
    page_icon="📍",
    layout="wide",
)

STATUS_OPTIONS = ["pending", "contactado", "interesado", "descartado", "landing_hecha"]
STATUS_COLORS = {
    "pending": "🟡",
    "contactado": "🔵",
    "interesado": "🟢",
    "descartado": "⚫",
    "landing_hecha": "✅",
}

WEBSITE_KIND_LABELS = {
    "none": "🚫 Sin nada",
    "instagram": "📸 Instagram",
    "facebook": "👥 Facebook",
    "tiktok": "🎵 TikTok",
    "linktree": "🌳 Linktree",
    "whatsapp": "💬 WhatsApp",
    "directory": "📒 Directorio",
    "real": "🌐 Web propia",
}


# ---------- Data loading ----------

@st.cache_data(ttl=30)
def load_leads() -> pd.DataFrame:
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT b.place_id, b.name, b.category, b.zone, b.address,
                   b.lat, b.lng, b.phone, b.website, b.website_kind,
                   b.rating, b.reviews_count, b.business_status, b.discovered_at,
                   p.has_website, p.analyzed, p.status as lead_status,
                   p.reviews_scraped, p.notes,
                   a.summary, a.strengths, a.pains, a.angles, a.tone,
                   a.target_audience, a.keywords, a.reviews_analyzed,
                   a.analyzed_at
            FROM businesses b
            JOIN pipeline_status p ON p.place_id = b.place_id
            LEFT JOIN analysis a ON a.place_id = b.place_id
            """
        ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    for col in ["strengths", "pains", "angles", "keywords"]:
        df[col] = df[col].apply(_parse_json)
    df["reviews_count"] = df["reviews_count"].fillna(0).astype(int)
    df["rating"] = df["rating"].fillna(0.0)
    df["lead_status"] = df["lead_status"].fillna("pending")
    df["status_label"] = df["lead_status"].map(
        lambda s: f"{STATUS_COLORS.get(s, '⚪')} {s}"
    )
    df["website_kind"] = df["website_kind"].fillna("none")
    df["kind_label"] = df["website_kind"].map(
        lambda k: WEBSITE_KIND_LABELS.get(k, k)
    )
    return df


def _parse_json(v: Any) -> list[str] | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, list):
        return v
    try:
        return json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return None


@st.cache_data(ttl=30)
def get_reviews(place_id: str) -> pd.DataFrame:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT author, rating, text, language, posted_at FROM reviews "
            "WHERE place_id = ? ORDER BY posted_at DESC",
            (place_id,),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def update_status(place_id: str, new_status: str, notes: str | None) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute(
            "UPDATE pipeline_status SET status = ?, notes = ?, updated_at = ? "
            "WHERE place_id = ?",
            (new_status, notes, now, place_id),
        )
    load_leads.clear()


# ---------- UI ----------

df = load_leads()

st.title("📍 Landing Leads — San Luis")

if df.empty:
    st.warning(
        "La base está vacía. Corré el discovery primero:\n\n"
        "`.venv/bin/python -m src.pipeline discover --zone san_luis --category peluqueria`"
    )
    st.stop()

# Top stats
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total negocios", len(df))
c2.metric("Sin web (leads)", int((df["has_website"] == 0).sum()))
c3.metric("Analizados", int((df["analyzed"] == 1).sum()))
c4.metric("Reviews bajadas", int(df["reviews_count"].sum()))
c5.metric("Categorías", df["category"].nunique())

st.divider()

# ---------- Sidebar filters ----------

with st.sidebar:
    st.header("Filtros")

    show_only_no_web = st.checkbox("Solo sin web propia (leads)", value=True)
    show_only_analyzed = st.checkbox("Solo analizados", value=False)

    kind_options = sorted(df["website_kind"].unique())
    kind_default = [k for k in kind_options if k != "real"]
    selected_kinds = st.multiselect(
        "Tipo de presencia digital",
        kind_options,
        default=kind_default,
        format_func=lambda k: WEBSITE_KIND_LABELS.get(k, k),
        help="Insta/FB/Linktree son leads premium: tenés contenido para inspirar la landing",
    )

    zones_available = sorted(df["zone"].unique())
    selected_zones = st.multiselect(
        "Zona", zones_available, default=zones_available
    )

    cats_available = sorted(df["category"].unique())
    selected_cats = st.multiselect(
        "Categoría", cats_available, default=cats_available
    )

    selected_statuses = st.multiselect(
        "Estado", STATUS_OPTIONS, default=STATUS_OPTIONS
    )

    max_reviews = max(int(df["reviews_count"].max()), 1)
    min_reviews = st.slider("Min reviews", 0, max_reviews, 0)

    min_rating = st.slider("Min rating", 0.0, 5.0, 0.0, 0.1)

    search = st.text_input("Buscar por nombre", "")

    st.divider()
    st.caption(f"DB: `{db.DB_PATH.name}`")
    if st.button("🔄 Refrescar"):
        load_leads.clear()
        st.rerun()


# ---------- Apply filters ----------

filtered = df.copy()
if show_only_no_web:
    filtered = filtered[filtered["has_website"] == 0]
if show_only_analyzed:
    filtered = filtered[filtered["analyzed"] == 1]
if selected_kinds:
    filtered = filtered[filtered["website_kind"].isin(selected_kinds)]
if selected_zones:
    filtered = filtered[filtered["zone"].isin(selected_zones)]
if selected_cats:
    filtered = filtered[filtered["category"].isin(selected_cats)]
if selected_statuses:
    filtered = filtered[filtered["lead_status"].isin(selected_statuses)]
filtered = filtered[filtered["reviews_count"] >= min_reviews]
filtered = filtered[filtered["rating"] >= min_rating]
if search:
    filtered = filtered[
        filtered["name"].str.contains(search, case=False, na=False)
    ]

filtered = filtered.sort_values("reviews_count", ascending=False).reset_index(drop=True)

# ---------- Tabs ----------

tab_table, tab_detail, tab_map = st.tabs(
    [f"📋 Tabla ({len(filtered)})", "🔍 Detalle del lead", "🗺️ Mapa"]
)

with tab_table:
    if filtered.empty:
        st.info("No hay resultados con esos filtros.")
    else:
        display_df = filtered[
            [
                "name",
                "category",
                "zone",
                "rating",
                "reviews_count",
                "kind_label",
                "phone",
                "address",
                "status_label",
                "analyzed",
            ]
        ].rename(
            columns={
                "name": "Nombre",
                "category": "Categoría",
                "zone": "Zona",
                "rating": "★",
                "reviews_count": "Reviews",
                "kind_label": "Presencia",
                "phone": "Teléfono",
                "address": "Dirección",
                "status_label": "Estado",
                "analyzed": "Analizado",
            }
        )
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "★": st.column_config.NumberColumn(format="%.1f ⭐"),
                "Reviews": st.column_config.NumberColumn(format="%d"),
                "Analizado": st.column_config.CheckboxColumn(),
            },
            height=600,
        )


with tab_detail:
    if filtered.empty:
        st.info("Aplicá filtros para encontrar un lead.")
    else:
        names_with_reviews = filtered.apply(
            lambda r: f"{r['name']} — {int(r['reviews_count'])}★ ({r['rating']})",
            axis=1,
        ).tolist()
        idx = st.selectbox(
            "Lead",
            range(len(names_with_reviews)),
            format_func=lambda i: names_with_reviews[i],
        )
        row = filtered.iloc[idx]

        col_info, col_actions = st.columns([3, 1])

        with col_info:
            st.subheader(row["name"])
            st.caption(
                f"{row['category']} • {row['zone']} • "
                f"{row['rating']:.1f}★ ({int(row['reviews_count'])} reviews)"
            )
            if row["address"]:
                st.text(f"📍 {row['address']}")
            if row["phone"]:
                st.text(f"📞 {row['phone']}")
            kind = row["website_kind"] or "none"
            kind_label = WEBSITE_KIND_LABELS.get(kind, kind)
            if kind == "none":
                st.markdown("🌐 **Sin presencia digital** — lead premium ✅")
            elif kind == "real":
                st.markdown(f"{kind_label}: [{row['website']}]({row['website']})")
            else:
                st.markdown(
                    f"{kind_label}: [{row['website']}]({row['website']}) "
                    f"— **lead** (usalo de inspiración para la landing)"
                )

        with col_actions:
            current_status = row["lead_status"] or "pending"
            new_status = st.selectbox(
                "Estado",
                STATUS_OPTIONS,
                index=STATUS_OPTIONS.index(current_status),
                key=f"status_{row['place_id']}",
            )
            new_notes = st.text_area(
                "Notas",
                value=row["notes"] or "",
                key=f"notes_{row['place_id']}",
                height=100,
            )
            if st.button("💾 Guardar", key=f"save_{row['place_id']}"):
                update_status(row["place_id"], new_status, new_notes or None)
                st.success("Guardado")
                st.rerun()

        st.divider()

        # Análisis Claude
        if row["analyzed"]:
            st.subheader("🧠 Análisis para landing")
            st.write(row["summary"])

            ca, cb = st.columns(2)
            with ca:
                st.markdown("**Fortalezas**")
                for s in row["strengths"] or []:
                    st.markdown(f"- ✅ {s}")
                st.markdown("**Audiencia**")
                st.text(row["target_audience"] or "—")

            with cb:
                st.markdown("**Dolores / quejas recurrentes**")
                for p in row["pains"] or []:
                    st.markdown(f"- ⚠️ {p}")
                st.markdown("**Tono recomendado**")
                st.text(row["tone"] or "—")

            st.markdown("**Ángulos copy-ready para la landing**")
            for a in row["angles"] or []:
                st.markdown(f"- 💡 _{a}_")

            st.markdown("**Keywords SEO locales**")
            kws = row["keywords"] or []
            if kws:
                st.markdown(" · ".join(f"`{k}`" for k in kws))

            st.caption(
                f"Analizado con {row.get('reviews_analyzed', '?')} reviews • "
                f"{row.get('analyzed_at', '')}"
            )
        else:
            st.info("Este lead todavía no fue analizado por Claude.")

        # Reviews crudas
        with st.expander(f"Reviews crudas ({int(row['reviews_count'])} totales)"):
            reviews_df = get_reviews(row["place_id"])
            if reviews_df.empty:
                st.text("Sin reviews capturadas todavía.")
            else:
                for _, rv in reviews_df.iterrows():
                    rating = rv["rating"] or 0
                    stars = "⭐" * int(rating)
                    st.markdown(f"**{rv['author']}** {stars}")
                    st.text(rv["text"] or "")
                    st.caption(rv["posted_at"] or "")
                    st.divider()


with tab_map:
    map_df = filtered[["lat", "lng"]].dropna().rename(columns={"lng": "lon"})
    if map_df.empty:
        st.info("No hay coordenadas para los filtros actuales.")
    else:
        st.map(map_df, zoom=12, use_container_width=True)
        st.caption(f"Mostrando {len(map_df)} negocios en el mapa")
