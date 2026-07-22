# Podcast reference catalogs

Two searchable "encyclopedias of references" served from this site:

- **Todopoderosos** → https://gragera.me/todopoderosos/
- **Aquí hay Dragones** → https://gragera.me/aqui-hay-dragones/

Both use a single shared frontend so they look and behave identically. The
design baseline is the original Todopoderosos site; Aquí hay Dragones was folded
into it (previously it had its own, older standalone build).

## How it works

```
catalogs/
  template.html        Shared, parametrized single-page app (dark/light,
                       search, category filters, accordion episodes, modal
                       with cover/description/Amazon, "más de <autor>",
                       deep-linkable references via #ref-<id>).
  build.py             Generator. Reads data/<slug>.json, writes
                       static/<slug>/{index.html,referencias.json}.
  data/
    todopoderosos.json       Enriched source data (episodes → references
    aqui-hay-dragones.json   with category/title/author/amazon_url/image/desc).
```

The frontend fetches `referencias.json` at runtime (a sibling of `index.html`),
so pages stay small and data can be updated without touching HTML.

Hugo copies everything under `static/` verbatim, so the generated
`static/<slug>/` folders are published as-is at `gragera.me/<slug>/`. The
generated files are committed (CI only runs `hugo`, not this script).

## Rebuilding

After updating any `data/<slug>.json`:

```bash
python3 catalogs/build.py
```

Then commit the changed files under `static/`.

## Data schema

`data/<slug>.json`:

```json
{
  "episodes": [
    {
      "episode": "TODOPODEROSOS #130 …",
      "references": [
        { "category": "🎬", "title": "…", "author": "…",
          "amazon_url": "https://…&tag=gragera-20",
          "image": "https://…",        // optional cover URL
          "desc": "…" }                 // optional description
      ]
    }
  ]
}
```

`build.py` assigns a stable sequential `id` to every reference (used for
deep-links and the "related by author" section) and normalizes the cover into
the `image` field. Per-podcast presentation (title, tagline, favicon, and the
episode-number/title regexes) lives in the `CATALOGS` dict in `build.py`.
