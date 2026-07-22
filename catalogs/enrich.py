#!/usr/bin/env python3
"""
Enrich reference catalogs with better art + info.

Pass 1 (this file): TMDB for 🎬 Cine and 📺 TV — the real poster and a Spanish
synopsis, replacing the often-tangential Wikipedia image.

For each unique (category, title, year) it searches TMDB, scores candidates on
title + year, and — only above a confidence gate — writes back into
catalogs/data/<slug>.json:  image (TMDB poster), desc (es overview, en
fallback), year, tmdb_id, tmdb_confidence.

Dedup + a cache (catalogs/data/.tmdb_cache.json, git-ignored) make it cheap and
re-runnable: the same film across many episodes is looked up once, and a second
run only touches what's new.

Usage:
    export TMDB_API_KEY=xxxx
    python3 catalogs/enrich.py --dry-run --sample 20      # eyeball matches
    python3 catalogs/enrich.py --slug todopoderosos       # apply for real
    python3 catalogs/enrich.py                             # both slugs
Then re-run build.py to regenerate the pages.
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
CACHE_PATH = os.path.join(DATA_DIR, ".tmdb_cache.json")

API = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w500"
CAT_MOVIE = "🎬"
CAT_TV = "📺"

TMDB_KEY = os.environ.get("TMDB_API_KEY", "").strip()


# ── HTTP ─────────────────────────────────────────────────────────────────────
def api_get(path, params):
    params = dict(params)
    params["api_key"] = TMDB_KEY
    url = API + path + "?" + urllib.parse.urlencode(params)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=25) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if e.code == 404:
                return None
            if e.code in (401, 403):
                sys.exit(f"TMDB auth failed (HTTP {e.code}). Check TMDB_API_KEY.")
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None


# ── text helpers ─────────────────────────────────────────────────────────────
def norm(s):
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def parse_title(raw):
    """('Concursante (2007)') -> ('Concursante', 2007)."""
    year = None
    m = re.search(r"\((\d{4})\)", raw)
    if m:
        year = int(m.group(1))
    title = re.sub(r"\s*\(.*?\)\s*$", "", raw).strip()
    return (title or raw).strip(), year


# ── matching ─────────────────────────────────────────────────────────────────
def search(kind, title, year):
    path = "/search/movie" if kind == "movie" else "/search/tv"
    params = {"query": title, "language": "es-ES", "include_adult": "false"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year
    data = api_get(path, params)
    return (data or {}).get("results", []) or []


def cand_fields(r, kind):
    if kind == "movie":
        title = r.get("title") or ""
        orig = r.get("original_title") or ""
        date = r.get("release_date") or ""
    else:
        title = r.get("name") or ""
        orig = r.get("original_name") or ""
        date = r.get("first_air_date") or ""
    cy = int(date[:4]) if date[:4].isdigit() else None
    return title, orig, cy


def classify(kind, title, year):
    """Return dict enrichment or {'miss': True}."""
    results = search(kind, title, year)
    # Fallback: retry without the year filter if nothing came back.
    if not results and year:
        results = search(kind, title, None)
    if not results:
        return {"miss": True}

    nt = norm(title)
    best = None
    for r in results:
        ct, co, cy = cand_fields(r, kind)
        title_exact = norm(ct) == nt or norm(co) == nt
        title_partial = (not title_exact) and (nt in norm(ct) or norm(ct) in nt) and len(nt) > 3
        if not (title_exact or title_partial):
            continue
        score = 3 if title_exact else 1
        if year and cy:
            d = abs(cy - year)
            score += 2 if d == 0 else (1 if d <= 1 else -1 if d <= 3 else -3)
        score += min(r.get("popularity", 0), 200) / 1000.0
        cand = (score, title_exact, cy, r)
        if best is None or cand[0] > best[0]:
            best = cand

    if best is None:
        return {"miss": True}
    score, title_exact, cy, r = best

    # Confidence gate
    if title_exact and (year is None or cy is None or abs(cy - year) <= 1):
        conf = "high"
    elif (title_exact and year and cy and abs(cy - year) <= 3) or (not title_exact and year and cy == year):
        conf = "medium"
    else:
        return {"miss": True}

    rid = r.get("id")
    ct, _, _ = cand_fields(r, kind)
    poster = r.get("poster_path")
    overview = (r.get("overview") or "").strip()
    if not overview and rid:  # Spanish overview often empty → English fallback
        det = api_get(f"/{'movie' if kind == 'movie' else 'tv'}/{rid}", {"language": "en-US"})
        if det:
            overview = (det.get("overview") or "").strip()

    return {
        "tmdb_id": rid,
        "kind": kind,
        "matched_title": ct,
        "year": cy,
        "image": (IMG_BASE + poster) if poster else None,
        "desc": overview or None,
        "confidence": conf,
    }


# ── driver ───────────────────────────────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0)


def unique_keys(episodes):
    """Yield (key, category, clean_title, year, sample_raw) for movie/TV refs."""
    seen = {}
    for ep in episodes:
        for r in ep["references"]:
            cat = r.get("category")
            if cat not in (CAT_MOVIE, CAT_TV):
                continue
            title, year = parse_title(r.get("title", ""))
            key = f"{cat}|{norm(title)}|{year or ''}"
            if key not in seen:
                seen[key] = (key, cat, title, year, r.get("title", ""))
    return list(seen.values())


def enrich_slug(slug, args, cache):
    path = os.path.join(DATA_DIR, f"{slug}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    keys = unique_keys(data["episodes"])

    # Optionally spread the sample across the whole set for a varied dry-run.
    todo = keys
    if args.sample:
        step = max(1, len(keys) // args.sample)
        todo = keys[::step][: args.sample]
    elif args.limit:
        todo = keys[: args.limit]

    stats = {"queried": 0, "high": 0, "medium": 0, "miss": 0, "cached": 0}
    print(f"\n=== {slug}: {len(keys)} unique movie/TV titles"
          + (f" (processing {len(todo)})" if todo is not keys else "") + " ===")

    for key, cat, title, year, raw in todo:
        kind = "movie" if cat == CAT_MOVIE else "tv"
        if key in cache and not args.refresh:
            res = cache[key]
            stats["cached"] += 1
        else:
            res = classify(kind, title, year)
            cache[key] = res
            stats["queried"] += 1
            time.sleep(0.03)

        if res.get("miss"):
            stats["miss"] += 1
            if args.dry_run:
                print(f"  ✗ {raw!r}  → no confident match")
            continue
        stats[res["confidence"]] += 1
        if args.dry_run:
            desc = (res.get("desc") or "")[:70]
            print(f"  ✓[{res['confidence'][:4]}] {raw!r} → "
                  f"{res['matched_title']!r} ({res.get('year')})  "
                  f"{'IMG' if res.get('image') else 'no-img'}  “{desc}…”")

    # Apply to every ref (fan the unique results back out) unless dry-run.
    applied = 0
    if not args.dry_run:
        for ep in data["episodes"]:
            for r in ep["references"]:
                cat = r.get("category")
                if cat not in (CAT_MOVIE, CAT_TV):
                    continue
                if args.only_missing and r.get("tmdb_id"):
                    continue
                title, year = parse_title(r.get("title", ""))
                res = cache.get(f"{cat}|{norm(title)}|{year or ''}")
                if not res or res.get("miss"):
                    continue
                if res.get("image"):
                    r["image"] = res["image"]
                if res.get("desc") and not r.get("desc"):
                    r["desc"] = res["desc"]
                if res.get("year"):
                    r["year"] = res["year"]
                r["tmdb_id"] = res["tmdb_id"]
                r["tmdb_confidence"] = res["confidence"]
                applied += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    print(f"  queried={stats['queried']} cached={stats['cached']} | "
          f"high={stats['high']} medium={stats['medium']} miss={stats['miss']}"
          + (f" | applied to {applied} refs" if not args.dry_run else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", choices=["todopoderosos", "aqui-hay-dragones"],
                    help="default: both")
    ap.add_argument("--dry-run", action="store_true", help="query + print, don't write")
    ap.add_argument("--sample", type=int, help="process a spread of N titles (for dry-run)")
    ap.add_argument("--limit", type=int, help="process only the first N unique titles")
    ap.add_argument("--only-missing", action="store_true",
                    help="skip refs already carrying a tmdb_id when applying")
    ap.add_argument("--refresh", action="store_true", help="ignore cache, re-query")
    args = ap.parse_args()

    if not TMDB_KEY:
        sys.exit("Set TMDB_API_KEY in the environment.")

    slugs = [args.slug] if args.slug else ["todopoderosos", "aqui-hay-dragones"]
    cache = load_cache()
    try:
        for slug in slugs:
            enrich_slug(slug, args, cache)
    finally:
        save_cache(cache)
        print(f"\ncache: {len(cache)} keys → {os.path.relpath(CACHE_PATH, ROOT)}")


if __name__ == "__main__":
    main()
