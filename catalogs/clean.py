#!/usr/bin/env python3
"""
Remove extraction noise from the catalogs: bare author/person mentions and
abstract "concept" entries that aren't actual works.

High precision by design — when in doubt it keeps the reference:
  • never removes anything TMDB already confirmed as a real film/TV (tmdb_id)
  • only treats a parenthetical qualifier as noise if it names a concept or a
    person-role (never a work type like novela/serie/álbum/banda sonora)
  • person detection needs a strong signal: title == author, a role-word author
    ("Compositor", "Obras de…"), or a multi-word name that also appears as an
    author elsewhere in the data

Dry-run by default; pass --apply to write. Re-run build.py afterwards.

    python3 catalogs/clean.py                 # dry-run, both slugs
    python3 catalogs/clean.py --apply
"""
import argparse
import json
import os
import re
import unicodedata
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")

CONCEPT_QUALS = {
    "concepto", "idea", "mito", "teoria", "termino", "fenomeno", "personaje",
    "lugar", "tiempo", "franquicia", "expresion", "palabra", "general", "generico",
    "movimiento", "estilo", "genero", "corriente",
}
PERSON_QUALS = {
    "director", "compositor", "dibujante", "guionista", "actor", "actriz",
    "escritor", "escritora", "artista", "cantante", "musico", "pintor",
    "autor", "autora", "banda", "grupo", "grupo musical", "poeta", "cineasta",
    "novelista", "dramaturgo", "realizador",
}
ROLE_AUTHORS = {
    "artista", "compositor", "director", "director de cine", "escritor", "escritora",
    "cantante", "musico", "dibujante", "guionista", "actor", "actriz", "pintor",
    "autor", "autora", "banda", "grupo", "grupo musical", "poeta", "dramaturgo",
    "novelista", "realizador", "cineasta", "grupo de rock", "grupo de musica",
}
ROLE_PREFIXES = ("obras de", "director de", "grupo ")


def norm(s):
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def trailing_qual(title):
    m = re.search(r"\(([^)]*)\)\s*$", title)
    if m and not re.fullmatch(r"\s*\d{4}\s*", m.group(1)):
        return norm(m.group(1))
    return None


def reason_to_remove(r, author_set):
    title = r.get("title", "") or ""
    author = r.get("author", "") or ""
    nt, na = norm(title), norm(author)
    q = trailing_qual(title)
    if q in CONCEPT_QUALS:
        return "concept"
    if q in PERSON_QUALS:
        return "person"
    if r.get("tmdb_id"):          # confirmed real work — keep
        return None
    if na and nt == na and (len(nt) >= 4 or " " in title.strip()):
        return "person"
    if na in ROLE_AUTHORS or any(na.startswith(p) for p in ROLE_PREFIXES):
        return "person"
    if " " in title.strip() and nt in author_set:
        return "person"
    return None


# A re-mention the extractor emitted as a separate entry: "(otra/tercera/…
# versión)" or "(reprise)". NOT "(versión 2023)" (a real remake — year kept).
VER_RE = re.compile(
    r"\s*\((?:otra|segunda|tercera|cuarta|quinta|sexta|s[eé]ptima|octava|novena|"
    r"d[eé]cima|nueva|[uú]ltima|\d+[ªa]?)?\s*versi[oó]n\)\s*$", re.I)
REPRISE_RE = re.compile(r"\s*\(reprise\)\s*$", re.I)


def canonical_title(title):
    """Strip re-mention suffixes and the artificial 'El cuento de ' prefix."""
    t = REPRISE_RE.sub("", VER_RE.sub("", title)).strip()
    # 'El cuento de Barba Azul' → 'Barba Azul', but only when it's clearly the
    # extractor's prefix on a proper title: next word capitalised and no year
    # (protects real titles like 'El cuento de la criada (1985)').
    m = re.match(r"^[Ee]l cuento de\s+(.+)$", t)
    if m and m.group(1)[:1].isupper() and not re.search(r"\(\d{4}\)", t):
        t = m.group(1).strip()
    return t or title


def process(slug, apply):
    path = os.path.join(DATA_DIR, f"{slug}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    refs = [r for e in data["episodes"] for r in e["references"]]
    author_set = {norm(r.get("author", "")) for r in refs if r.get("author")}
    author_set.discard("")

    counts = Counter()
    samples = {"concept": [], "person": [], "dedup": []}
    kept = deduped = retitled = 0
    for ep in data["episodes"]:
        keep = []
        for r in ep["references"]:
            why = reason_to_remove(r, author_set)
            if why:
                counts[why] += 1
                if len(samples[why]) < 15:
                    samples[why].append(f"{r.get('title','')} | {r.get('author','')}")
            else:
                keep.append(r)
        # collapse re-mentions + canonicalise titles, within the episode
        seen, out = set(), []
        for r in keep:
            ct = canonical_title(r.get("title", ""))
            k = (norm(ct), norm(r.get("author", "")), r.get("category", ""))
            if k in seen:
                deduped += 1
                if len(samples["dedup"]) < 15:
                    samples["dedup"].append(f"{r.get('title','')} → (dup of {ct!r})")
                continue
            seen.add(k)
            if ct != r.get("title", ""):
                retitled += 1
                if apply:
                    r["title"] = ct
            out.append(r)
        kept += len(out)
        if apply:
            ep["references"] = out

    total = len(refs)
    removed = counts["concept"] + counts["person"]
    print(f"\n=== {slug}: {total} refs → remove {removed} "
          f"(concept {counts['concept']}, person {counts['person']}), "
          f"dedup {deduped}, retitle {retitled}, keep {kept} ===")
    for why in ("concept", "person", "dedup"):
        if samples[why]:
            print(f"  [{why}] examples:")
            for s in samples[why][:10]:
                print(f"     - {s}")

    if apply:
        data["total_references"] = kept
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"  written: {kept} refs remain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", choices=["todopoderosos", "aqui-hay-dragones"], help="default: both")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()
    slugs = [args.slug] if args.slug else ["todopoderosos", "aqui-hay-dragones"]
    for slug in slugs:
        process(slug, args.apply)
    if not args.apply:
        print("\n(dry-run — pass --apply to write)")


if __name__ == "__main__":
    main()
