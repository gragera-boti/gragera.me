#!/usr/bin/env python3
"""
Build the podcast reference catalogs served from gragera.me.

Reads the enriched source data in catalogs/data/<slug>.json and a single shared
template (catalogs/template.html), and writes a self-contained page +
referencias.json into static/<slug>/ for each podcast. Hugo copies static/
verbatim, so the pages end up at https://gragera.me/<slug>/.

Run from anywhere:  python3 catalogs/build.py
"""
import json
import os
import re
import shutil
from html import escape

ROOT = os.path.dirname(os.path.abspath(__file__))          # .../gragera.me/catalogs
SITE = os.path.dirname(ROOT)                                # .../gragera.me
DATA_DIR = os.path.join(ROOT, "data")
TEMPLATE = os.path.join(ROOT, "template.html")
STATIC = os.path.join(SITE, "static")

# ── Per-podcast configuration ────────────────────────────────────────────────
CATALOGS = {
    "todopoderosos": {
        "source": "todopoderosos.json",
        "covers_dir": None,
        "favicon": "🎙️",
        "badge": "Podcast · 2014–2026",
        "h1_main": "Todopoderosos",
        "h1_sub": "Enciclopedia de Referencias",
        "tagline": ("Todas las películas, series, libros, cómics, canciones y videojuegos "
                    "mencionados en los episodios del podcast. Una arqueología de la cultura pop."),
        "podcast_url": "https://podcasts.apple.com/es/podcast/todopoderosos/id953023656",
        "podcast_name": "Todopoderosos",
        "js": {
            "epNumRe": r"#(\d+)",
            "epNumPrefix": "#",
            "epStripRe": r"^TODOPODEROSOS\s*",
            "epBonusLabel": "Bonus",
        },
    },
    "aqui-hay-dragones": {
        "source": "aqui-hay-dragones.json",
        "covers_dir": os.path.join(ROOT, "covers-aqui-hay-dragones"),
        "favicon": "🐉",
        "badge": "Podcast",
        "h1_main": "Aquí hay Dragones",
        "h1_sub": "Enciclopedia de Referencias",
        "tagline": ("Todas las películas, series, libros, cómics, canciones y videojuegos "
                    "mencionados en el podcast con Arturo González-Campos, Javier Cansado, "
                    "Juan Gómez-Jurado y Rodrigo Cortés."),
        "podcast_url": "https://www.ivoox.com/podcast-aqui-hay-dragones_sq_f1900735_1.html",
        "podcast_name": "Aquí hay Dragones",
        "js": {
            "epNumRe": r"AHD\s*(\d+)",
            "epNumPrefix": "",
            "epStripRe": r"^AHD\s*\d+\s*[-–—]\s*",
            "epBonusLabel": "Bonus",
        },
    },
}

REF_FIELDS = ("category", "title", "author", "amazon_url", "image", "desc", "id")


def normalize(slug, cfg, template):
    src = os.path.join(DATA_DIR, cfg["source"])
    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    out_dir = os.path.join(STATIC, slug)
    os.makedirs(out_dir, exist_ok=True)

    covers_src = cfg["covers_dir"]
    copied_covers = set()
    next_id = 0
    total_refs = 0

    episodes_out = []
    for ep in data["episodes"]:
        refs_out = []
        for r in ep["references"]:
            # Resolve the cover image: remote URL, or a local file we can ship.
            image = r.get("image") or ""
            if not image:
                cover = r.get("cover") or ""
                if cover.startswith("http"):
                    image = cover
                elif cover and covers_src:
                    name = os.path.basename(cover)
                    if os.path.exists(os.path.join(covers_src, name)):
                        image = "covers/" + name
                        copied_covers.add(name)

            ref = {
                "id": r["id"] if isinstance(r.get("id"), int) else next_id,
                "category": r.get("category", "📄"),
                "title": r.get("title", ""),
                "author": r.get("author", ""),
                "amazon_url": r.get("amazon_url", ""),
            }
            if image:
                ref["image"] = image
            if r.get("desc"):
                ref["desc"] = r["desc"]
            next_id = max(next_id, ref["id"]) + 1
            refs_out.append(ref)
            total_refs += 1
        episodes_out.append({"episode": ep["episode"], "references": refs_out})

    out_data = {
        "total_episodes": len(episodes_out),
        "total_references": total_refs,
        "episodes": episodes_out,
    }
    with open(os.path.join(out_dir, "referencias.json"), "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, separators=(",", ":"))

    # Ship any local covers we actually referenced.
    if copied_covers:
        dst = os.path.join(out_dir, "covers")
        os.makedirs(dst, exist_ok=True)
        for name in copied_covers:
            shutil.copy2(os.path.join(covers_src, name), os.path.join(dst, name))

    # Render the page from the shared template.
    js_cfg = json.dumps(cfg["js"], ensure_ascii=False)
    page = (template
            .replace("__TITLE__", escape(f'{cfg["h1_main"]} · {cfg["h1_sub"]}'))
            .replace("__FAVICON__", cfg["favicon"])
            .replace("__BADGE__", escape(cfg["badge"]))
            .replace("__H1_MAIN__", escape(cfg["h1_main"]))
            .replace("__H1_SUB__", escape(cfg["h1_sub"]))
            .replace("__TAGLINE__", escape(cfg["tagline"]))
            .replace("__PODCAST_URL__", escape(cfg["podcast_url"], quote=True))
            .replace("__PODCAST_NAME__", escape(cfg["podcast_name"]))
            .replace("__CONFIG_JSON__", js_cfg))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)

    print(f"  {slug:20s} {len(episodes_out):>4} episodes  {total_refs:>6,} refs"
          f"  {len(copied_covers):>3} local covers")


def main():
    with open(TEMPLATE, encoding="utf-8") as f:
        template = f.read()
    print("Building catalogs → static/")
    for slug, cfg in CATALOGS.items():
        normalize(slug, cfg, template)
    print("Done.")


if __name__ == "__main__":
    main()
