#!/usr/bin/env python3
"""
Enrich reference catalogs with better art + info.

Four sources, same machinery (dedup + confidence gate + resumable cache):
  --source tmdb   🎬 Cine + 📺 TV  → TMDB poster + Spanish synopsis (en fallback)
  --source books  📚 Libros        → Google Books Spanish synopsis + ISBN,
                                      Open Library cover-by-ISBN (art upgrade),
                                      Google thumbnail as cover fallback
  --source comics 📖 Cómics        → Google Books first (Spanish), Comic Vine
                                      fallback for what Books lacks (English art)
  --source music  🎵 Música        → Spotify album/track cover art (no synopsis)

Keys (read from env, never committed): TMDB_API_KEY, GOOGLE_BOOKS_API_KEY,
COMIC_VINE_API_KEY, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET.

Matches are only written above a confidence gate (movies: title+year; books:
title+author), so uncertain items keep their existing image/placeholder rather
than getting a wrong cover. Results are written into catalogs/data/<slug>.json
(image, desc, + traceability metadata) and a git-ignored cache makes re-runs
cheap and lets a run that hits a daily API quota resume later.

Usage:
    export TMDB_API_KEY=xxxx GOOGLE_BOOKS_API_KEY=yyyy
    python3 catalogs/enrich.py --source books --dry-run --sample 20
    python3 catalogs/enrich.py --source books --slug todopoderosos
    python3 catalogs/enrich.py --source books            # both slugs
Then re-run build.py to regenerate the pages.
"""
import argparse
import base64
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
PROGRESS_PATH = os.path.join(DATA_DIR, ".enrich_progress.json")
UA = "gragera.me-catalog-enricher/1.0 (https://gragera.me)"

CAT_MOVIE, CAT_TV, CAT_BOOK, CAT_COMIC, CAT_MUSIC, CAT_GAME = "🎬", "📺", "📚", "📖", "🎵", "🎮"


class DailyQuota(Exception):
    pass


# ── HTTP ─────────────────────────────────────────────────────────────────────
def http_json(url, headers=None):
    """Return (data, reason). reason in {None,'rate','daily','notfound','auth','err'}."""
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r), None
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")
            except Exception:
                pass
            if e.code == 404:
                return None, "notfound"
            if e.code in (403, 429):
                if "dailyLimitExceeded" in body or "per day" in body.lower():
                    return None, "daily"
                if attempt == 4:
                    return None, "ratelimit"       # exhausted → stop, don't cache a miss
                time.sleep(2 * (attempt + 1))
                continue
            if e.code in (500, 502, 503, 504):     # transient server/throttle blips
                if attempt == 4:
                    return None, "ratelimit"
                time.sleep(2 * (attempt + 1))
                continue
            if e.code in (401,):
                sys.exit(f"Auth failed (HTTP {e.code}) for {url.split('?')[0]}")
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None, "err"


# ── text helpers ─────────────────────────────────────────────────────────────
def norm(s):
    s = (s or "").lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def strip_parens(raw):
    s = (re.sub(r"\s*\(.*?\)\s*$", "", raw).strip() or raw)
    return s.strip().strip('"“”\'').strip()          # also drop wrapping quotes


def parse_year(raw):
    m = re.search(r"\((\d{4})\)", raw)
    return int(m.group(1)) if m else None


def clean_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()


# ════════════════════════════════════════════════════════════════════════════
# TMDB source (🎬 movies, 📺 TV)
# ════════════════════════════════════════════════════════════════════════════
TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w500"


def tmdb_key():
    k = os.environ.get("TMDB_API_KEY", "").strip()
    if not k:
        sys.exit("Set TMDB_API_KEY in the environment.")
    return k


def tmdb_itemkey(ref):
    title = strip_parens(ref.get("title", ""))
    year = parse_year(ref.get("title", ""))
    kind = "movie" if ref.get("category") == CAT_MOVIE else "tv"
    key = f"{ref['category']}|{norm(title)}|{year or ''}"
    return key, {"kind": kind, "title": title, "year": year}


def tmdb_search(kind, title, year, key):
    path = "/search/movie" if kind == "movie" else "/search/tv"
    params = {"api_key": key, "query": title, "language": "es-ES", "include_adult": "false"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year
    data, _ = http_json(TMDB_API + path + "?" + urllib.parse.urlencode(params))
    return (data or {}).get("results", []) or []


def tmdb_cand(r, kind):
    if kind == "movie":
        t, o, d = r.get("title") or "", r.get("original_title") or "", r.get("release_date") or ""
    else:
        t, o, d = r.get("name") or "", r.get("original_name") or "", r.get("first_air_date") or ""
    cy = int(d[:4]) if d[:4].isdigit() else None
    return t, o, cy


def tmdb_classify(meta):
    kind, title, year = meta["kind"], meta["title"], meta["year"]
    key = tmdb_key()
    results = tmdb_search(kind, title, year, key) or (tmdb_search(kind, title, None, key) if year else [])
    if not results:
        return {"miss": True}
    nt = norm(title)
    best = None
    for r in results:
        ct, co, cy = tmdb_cand(r, kind)
        exact = norm(ct) == nt or norm(co) == nt
        part = (not exact) and len(nt) > 3 and (nt in norm(ct) or norm(ct) in nt)
        if not (exact or part):
            continue
        score = (3 if exact else 1)
        if year and cy:
            d = abs(cy - year)
            score += 2 if d == 0 else (1 if d <= 1 else -1 if d <= 3 else -3)
        score += min(r.get("popularity", 0), 200) / 1000.0
        if best is None or score > best[0]:
            best = (score, exact, cy, r)
    if best is None:
        return {"miss": True}
    _, exact, cy, r = best
    if exact and (year is None or cy is None or abs(cy - year) <= 1):
        conf = "high"
    elif (exact and year and cy and abs(cy - year) <= 3):
        conf = "medium"
    else:
        return {"miss": True}
    ct, _, _ = tmdb_cand(r, kind)
    poster, overview, rid = r.get("poster_path"), (r.get("overview") or "").strip(), r.get("id")
    if not overview and rid:
        det, _ = http_json(f"{TMDB_API}/{kind}/{rid}?" + urllib.parse.urlencode(
            {"api_key": key, "language": "en-US"}))
        overview = (det or {}).get("overview", "").strip()
    return {"tmdb_id": rid, "matched_title": ct, "year": cy,
            "image": (TMDB_IMG + poster) if poster else None,
            "desc": overview or None, "confidence": conf}


def tmdb_apply(ref, res):
    if res.get("image"):
        ref["image"] = res["image"]
    if res.get("desc") and not ref.get("desc"):
        ref["desc"] = res["desc"]
    if res.get("year"):
        ref["year"] = res["year"]
    ref["tmdb_id"] = res.get("tmdb_id")
    ref["tmdb_confidence"] = res["confidence"]


# ════════════════════════════════════════════════════════════════════════════
# Books source (📚 Google Books + Open Library covers)
# ════════════════════════════════════════════════════════════════════════════
GBOOKS_API = "https://www.googleapis.com/books/v1/volumes"


def books_key():
    k = os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip()
    if not k:
        sys.exit("Set GOOGLE_BOOKS_API_KEY in the environment.")
    return k


# Parenthetical qualifiers the extractor adds to mean "mentioned as a concept,
# not a specific titled work" — never a real book, so don't try to match them.
BOOK_SKIP_QUAL = {"concepto", "idea", "planeta", "personaje", "termino", "expresion",
                  "palabra", "mito", "general", "generico", "fenomeno", "teoria"}


def books_itemkey(ref):
    raw = ref.get("title", "")
    title = strip_parens(raw)
    author = ref.get("author", "") or ""
    qual = None
    m = re.search(r"\(([^)]*)\)\s*$", raw)
    if m and not re.fullmatch(r"\s*\d{4}\s*", m.group(1)):
        qual = norm(m.group(1))
    key = f"{CAT_BOOK}|{norm(title)}|{norm(author)}"
    return key, {"title": title, "author": author, "qual": qual}


def gbooks_search(title, author, lang, key):
    q = f"intitle:{title}"
    if author:
        q += f" inauthor:{author}"
    params = {"q": q, "maxResults": 5, "printType": "books", "country": "ES", "key": key}
    if lang:
        params["langRestrict"] = lang
    data, reason = http_json(GBOOKS_API + "?" + urllib.parse.urlencode(params))
    if reason in ("daily", "ratelimit"):       # throttled → stop; never cache a false miss
        raise DailyQuota()
    return (data or {}).get("items", []) or []


def isbn_of(vi):
    ids = vi.get("industryIdentifiers", []) or []
    for want in ("ISBN_13", "ISBN_10"):
        for i in ids:
            if i.get("type") == want:
                return i.get("identifier")
    return None


def gbooks_thumb(vi):
    il = vi.get("imageLinks") or {}
    u = il.get("thumbnail") or il.get("smallThumbnail")
    if not u:
        return None
    return u.replace("http://", "https://").replace("&edge=curl", "")


def ol_cover(isbn):
    if not isbn:
        return None
    base = f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg"
    req = urllib.request.Request(base + "?default=false", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            if r.status == 200:
                return base
    except Exception:
        return None
    return None


def pick_book(items, title, author):
    nt, na = norm(title), norm(author)
    best = None
    for it in items:
        vi = it.get("volumeInfo", {})
        ct = norm(vi.get("title", ""))
        cst = norm((vi.get("title", "") + " " + vi.get("subtitle", "")))
        authors = [norm(a) for a in (vi.get("authors") or [])]
        exact = ct == nt or cst == nt
        part = (not exact) and len(nt) > 3 and (nt in cst or (len(ct) > 4 and ct in nt))
        if not (exact or part):
            continue
        amatch = bool(na) and any(na in a or a in na for a in authors)
        score = (3 if exact else 1) + (2 if amatch else 0)
        if best is None or score > best[0]:
            best = (score, exact, part, amatch, vi)
    if best is None:
        return None, None
    _, exact, part, amatch, vi = best
    if na:
        if amatch and exact:
            conf = "high"
        elif amatch and part:
            conf = "medium"
        else:
            return None, None
    else:
        conf = "medium" if exact else None
        if conf is None:
            return None, None
    return vi, conf


def gbooks_lookup(title, author):
    """Shared Google Books match (Spanish-first). Returns result dict or None."""
    key = books_key()
    chosen, chosen_conf = None, None
    # Spanish first, accept on match to save quota; only fall back to a global
    # search when the Spanish query found nothing at all.
    for lang in ("es", None):
        items = gbooks_search(title, author, lang, key)
        time.sleep(0.05)
        vi, conf = pick_book(items, title, author)
        if vi:
            chosen, chosen_conf = vi, conf
            break
    if chosen is None:
        return None
    isbn = isbn_of(chosen)
    # Google thumbnail is reliable and free; Open Library (archive.org) is a
    # higher-res upgrade but slow/flaky, so only reach for it when there's no
    # thumbnail, and with a short timeout that fails fast.
    return {"source": "books", "matched_title": chosen.get("title", ""), "isbn": isbn,
            "image": gbooks_thumb(chosen) or ol_cover(isbn),
            "desc": clean_html(chosen.get("description", "")) or None, "confidence": chosen_conf}


def books_classify(meta):
    if meta.get("qual") in BOOK_SKIP_QUAL:
        return {"miss": True}
    return gbooks_lookup(meta["title"], meta["author"]) or {"miss": True}


def books_apply(ref, res):
    if res.get("image"):
        ref["image"] = res["image"]
    if res.get("desc") and not ref.get("desc"):
        ref["desc"] = res["desc"]
    if res.get("isbn"):
        ref["isbn"] = res["isbn"]
    ref["book_confidence"] = res["confidence"]


# ════════════════════════════════════════════════════════════════════════════
# Comics source (📖 Comic Vine)
# ════════════════════════════════════════════════════════════════════════════
CV_API = "https://comicvine.gamespot.com/api"
CV_FIELDS = "id,name,image,deck,description,start_year,publisher,count_of_issues"


def comics_key():
    k = os.environ.get("COMIC_VINE_API_KEY", "").strip()
    if not k:
        sys.exit("Set COMIC_VINE_API_KEY in the environment.")
    return k


def comics_itemkey(ref):
    title = strip_parens(ref.get("title", ""))
    author = ref.get("author", "") or ""
    key = f"{CAT_COMIC}|{norm(title)}|{norm(author)}"
    return key, {"title": title, "author": author}


def cv_search(title, key):
    params = {"api_key": key, "format": "json", "query": title,
              "resources": "volume", "limit": 10, "field_list": CV_FIELDS}
    url = CV_API + "/search/?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        data, reason = http_json(url)
        sc = (data or {}).get("status_code")
        if sc == 100:
            sys.exit("Comic Vine: invalid API key (status 100).")
        if reason in ("daily", "ratelimit") or sc == 107:      # rate limit
            if attempt < 3:
                time.sleep(30 * (attempt + 1))
                continue
            raise DailyQuota()
        return (data or {}).get("results", []) or []
    return []


def cv_image(v):
    img = v.get("image") or {}
    for k in ("super_url", "screen_large_url", "medium_url", "screen_url"):
        u = img.get(k)
        if u and "blank" not in u:
            return u
    return None


def pick_comic(items, title):
    nt = norm(title)
    best = None
    for v in items:
        cn = norm(v.get("name", ""))
        exact = cn == nt
        part = (not exact) and len(nt) > 4 and (nt in cn or cn in nt)
        if not (exact or part):
            continue
        score = (3 if exact else 1) + min(v.get("count_of_issues") or 0, 100) / 1000.0
        if best is None or score > best[0]:
            best = (score, exact, v)
    if best is None:
        return None, None
    _, exact, v = best
    return v, ("high" if exact else "medium")


def comics_classify(meta):
    # Spanish first via Google Books (graphic novels are well covered there);
    # Comic Vine only as fallback for what Books doesn't have (English art/info).
    r = gbooks_lookup(meta["title"], meta["author"])
    if r:
        return r
    key = comics_key()
    items = cv_search(meta["title"], key)
    time.sleep(1.0)                              # be polite to Comic Vine
    v, conf = pick_comic(items, meta["title"])
    if not v:
        return {"miss": True}
    desc = (v.get("deck") or "").strip() or clean_html(v.get("description", ""))
    return {"source": "comicvine", "cv_id": v.get("id"), "matched_title": v.get("name", ""),
            "image": cv_image(v), "desc": desc or None, "confidence": conf}


def comics_apply(ref, res):
    if res.get("image"):
        ref["image"] = res["image"]
    if res.get("desc") and not ref.get("desc"):
        ref["desc"] = res["desc"]
    ref["cv_id"] = res.get("cv_id")
    ref["comic_confidence"] = res["confidence"]


# ════════════════════════════════════════════════════════════════════════════
# Music source (🎵 Spotify — album/track cover art; Spotify has no synopsis)
# ════════════════════════════════════════════════════════════════════════════
SP_TOKEN = {"val": None, "exp": 0.0}


def spotify_token():
    now = time.time()
    if SP_TOKEN["val"] and now < SP_TOKEN["exp"] - 60:
        return SP_TOKEN["val"]
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    sec = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and sec):
        sys.exit("Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.")
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token", data=body,
        headers={"Authorization": "Basic " + auth,
                 "Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.load(r)
    except Exception as e:
        sys.exit(f"Spotify auth failed: {e}")
    SP_TOKEN["val"] = j["access_token"]
    SP_TOKEN["exp"] = now + j.get("expires_in", 3600)
    return SP_TOKEN["val"]


def sp_search(q, typ):
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode(
        {"q": q, "type": typ, "limit": 10, "market": "ES"})
    data, _ = http_json(url, {"Authorization": "Bearer " + spotify_token()})
    return data or {}


def music_itemkey(ref):
    title = strip_parens(ref.get("title", ""))
    author = ref.get("author", "") or ""
    key = f"{CAT_MUSIC}|{norm(title)}|{norm(author)}"
    return key, {"title": title, "author": author}


def sp_pick(items, title, author):
    nt, na = norm(title), norm(author)
    best = None
    for it in items:
        cn = norm(it.get("name", ""))
        artists = [norm(a.get("name", "")) for a in (it.get("artists") or [])]
        exact = cn == nt
        part = (not exact) and len(nt) > 3 and (nt in cn or cn in nt)
        if not (exact or part):
            continue
        amatch = bool(na) and any(na in a or a in na for a in artists)
        score = (3 if exact else 1) + (2 if amatch else 0) + min(it.get("popularity", 0), 100) / 1000.0
        if best is None or score > best[0]:
            best = (score, exact, amatch, it)
    if best is None:
        return None, None
    _, exact, amatch, it = best
    if na and not amatch and not exact:          # author given but nothing lines up
        return None, None
    return it, ("high" if (exact and (amatch or not na)) else "medium")


def music_classify(meta):
    title, author = meta["title"], meta["author"]
    q = f"album:{title}" + (f" artist:{author}" if author else "")
    albums = sp_search(q, "album").get("albums", {}).get("items", [])
    time.sleep(0.05)
    alb, conf = sp_pick(albums, title, author)
    if alb:
        return {"source": "spotify", "sp_id": alb.get("id"), "matched_title": alb.get("name", ""),
                "image": (alb.get("images") or [{}])[0].get("url"), "desc": None, "confidence": conf}
    q = f"track:{title}" + (f" artist:{author}" if author else "")
    tracks = sp_search(q, "track").get("tracks", {}).get("items", [])
    time.sleep(0.05)
    tr, conf = sp_pick(tracks, title, author)
    if tr:
        imgs = (tr.get("album") or {}).get("images") or [{}]
        return {"source": "spotify", "sp_id": tr.get("id"), "matched_title": tr.get("name", ""),
                "image": imgs[0].get("url"), "desc": None, "confidence": conf}
    return {"miss": True}


def music_apply(ref, res):
    if res.get("image"):
        ref["image"] = res["image"]
    ref["sp_id"] = res.get("sp_id")
    ref["music_confidence"] = res["confidence"]


# ════════════════════════════════════════════════════════════════════════════
# Wikipedia source (📚📖🎮 — Spanish description + correct article image, no key/quota)
# ════════════════════════════════════════════════════════════════════════════
WIKI_API = "https://es.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://es.wikipedia.org/api/rest_v1/page/summary/"


def wiki_key():
    return ""            # no key/quota — Wikipedia read API only needs a UA


def wiki_itemkey(ref):
    title = strip_parens(ref.get("title", ""))
    author = ref.get("author", "") or ""
    key = f"wiki|{ref.get('category')}|{norm(title)}|{norm(author)}"
    return key, {"title": title, "author": author}


def wiki_search(query):
    url = WIKI_API + "?" + urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query,
        "format": "json", "srlimit": 5, "srnamespace": 0})
    data, _ = http_json(url)
    return [r["title"] for r in ((data or {}).get("query", {}).get("search", []) or [])]


def wiki_summary(page_title):
    url = WIKI_SUMMARY + urllib.parse.quote(page_title.replace(" ", "_"), safe="")
    data, _ = http_json(url)
    return data


# A book/comic/game should never resolve to a film/series/song article.
WIKI_WRONG_MEDIA = re.compile(
    r"\((?:pel[ií]cula|film|serie|serie de televisi[oó]n|miniserie|telenovela|"
    r"canci[oó]n|[aá]lbum|banda sonora|[oó]pera|programa[^)]*)\)\s*$", re.I)


def wiki_build(s, conf):
    img = (s.get("thumbnail") or {}).get("source") or (s.get("originalimage") or {}).get("source")
    extract = (s.get("extract") or "").strip()
    return {"source": "wiki", "matched_title": s.get("title", ""),
            "image": img, "desc": extract or None, "confidence": conf}


def wiki_lookup(query, nt):
    """Return (summary, conf) — exact-title match preferred over partial."""
    cands = wiki_search(query)
    time.sleep(0.1)
    best = None
    for pt in cands[:5]:
        if WIKI_WRONG_MEDIA.search(pt):          # wrong medium for a text/game ref
            continue
        s = wiki_summary(pt)
        time.sleep(0.1)
        if not s or s.get("type") == "disambiguation":
            continue
        if not ((s.get("extract") or "").strip() or (s.get("thumbnail") or {}).get("source")):
            continue
        st = norm(s.get("title", ""))
        if st == nt:
            return s, "high"
        if best is None and len(nt) > 3 and (nt in st or st in nt):
            best = s
    return (best, "medium") if best else (None, None)


def wiki_classify(meta):
    title, author = meta["title"], meta["author"]
    nt = norm(title)
    # Title alone surfaces Wikipedia's primary topic (the work itself); only add
    # the author to disambiguate when the title alone didn't find an exact hit.
    s, conf = wiki_lookup(title, nt)
    if conf != "high" and author:
        s2, conf2 = wiki_lookup(title + " " + author, nt)
        if conf2 == "high" or (conf2 and not conf):
            s, conf = s2, conf2
    return wiki_build(s, conf) if s else {"miss": True}


def wiki_apply(ref, res):
    # Keep a trusted cover (TMDB/Spotify/Google Books) but still take Wikipedia's
    # Spanish description; otherwise replace the unreliable original image too.
    trusted = ref.get("book_confidence") or ref.get("tmdb_id") or \
        ref.get("sp_id") or ref.get("comic_confidence")
    if res.get("image") and not trusted:
        ref["image"] = res["image"]
    if res.get("desc") and not ref.get("desc"):
        ref["desc"] = res["desc"]
    ref["wiki_confidence"] = res["confidence"]


# ── source registry ──────────────────────────────────────────────────────────
SOURCES = {
    "tmdb": {"cats": (CAT_MOVIE, CAT_TV), "itemkey": tmdb_itemkey,
             "classify": tmdb_classify, "apply": tmdb_apply, "id_field": "tmdb_id",
             "label": "movie/TV"},
    "books": {"cats": (CAT_BOOK,), "itemkey": books_itemkey,
              "classify": books_classify, "apply": books_apply, "id_field": "book_confidence",
              "label": "book"},
    "comics": {"cats": (CAT_COMIC,), "itemkey": comics_itemkey,
               "classify": comics_classify, "apply": comics_apply, "id_field": "comic_confidence",
               "label": "comic"},
    "music": {"cats": (CAT_MUSIC,), "itemkey": music_itemkey,
              "classify": music_classify, "apply": music_apply, "id_field": "sp_id",
              "label": "music"},
    "wiki": {"cats": (CAT_BOOK, CAT_COMIC, CAT_GAME), "itemkey": wiki_itemkey,
             "classify": wiki_classify, "apply": wiki_apply, "id_field": "wiki_confidence",
             "label": "book/comic/game"},
}


# ── driver ───────────────────────────────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=0)
    os.replace(tmp, CACHE_PATH)          # atomic — never leaves a half-written cache


def write_progress(source, slug, done, total, st):
    p = {"source": source, "slug": slug, "done": done, "total": total,
         "hit": st["high"] + st["medium"], "miss": st["miss"],
         "pct": round(100 * done / total, 1) if total else 0,
         "ts": time.strftime("%H:%M:%S")}
    tmp = PROGRESS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False)
    os.replace(tmp, PROGRESS_PATH)


def unique_keys(episodes, src):
    seen = {}
    for ep in episodes:
        for r in ep["references"]:
            if r.get("category") not in src["cats"]:
                continue
            key, meta = src["itemkey"](r)
            if key not in seen:
                meta["_raw"] = r.get("title", "")
                seen[key] = (key, meta)
    return list(seen.values())


def enrich_slug(slug, args, cache, src):
    path = os.path.join(DATA_DIR, f"{slug}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    keys = unique_keys(data["episodes"], src)

    todo = keys
    if args.sample:
        step = max(1, len(keys) // args.sample)
        todo = keys[::step][: args.sample]
    elif args.limit:
        todo = keys[: args.limit]

    st = {"queried": 0, "cached": 0, "high": 0, "medium": 0, "miss": 0}
    print(f"\n=== {slug} [{args.source}]: {len(keys)} unique {src['label']} titles"
          + (f" (processing {len(todo)})" if todo is not keys else "") + " ===")

    hit_quota = False
    processed = 0
    for key, meta in todo:
        if key in cache and not args.refresh:
            res = cache[key]
            st["cached"] += 1
        else:
            try:
                res = src["classify"](meta)
            except DailyQuota:
                print("  ⚠ daily API quota reached — stopping; re-run later to resume.")
                hit_quota = True
                break
            cache[key] = res
            st["queried"] += 1
        processed += 1
        if processed % 25 == 0:               # periodic flush → crash-safe + live progress
            save_cache(cache)
            write_progress(args.source, slug, processed, len(todo), st)
        if res.get("miss"):
            st["miss"] += 1
            if args.dry_run:
                print(f"  ✗ {meta['_raw']!r} — no confident match")
            continue
        st[res["confidence"]] += 1
        if args.dry_run:
            d = (res.get("desc") or "")[:66]
            print(f"  ✓[{res['confidence'][:4]}] {meta['_raw']!r} → {res['matched_title']!r}  "
                  f"{'IMG' if res.get('image') else 'no-img'}  “{d}…”")

    write_progress(args.source, slug, len(todo), len(todo), st)
    applied = 0
    if not args.dry_run:
        for ep in data["episodes"]:
            for r in ep["references"]:
                if r.get("category") not in src["cats"]:
                    continue
                if args.only_missing and r.get(src["id_field"]) is not None:
                    continue
                key, _ = src["itemkey"](r)
                res = cache.get(key)
                if not res or res.get("miss"):
                    continue
                src["apply"](r, res)
                applied += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    print(f"  queried={st['queried']} cached={st['cached']} | high={st['high']} "
          f"medium={st['medium']} miss={st['miss']}"
          + (f" | applied to {applied} refs" if not args.dry_run else ""))
    return hit_quota


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=list(SOURCES), default="tmdb")
    ap.add_argument("--slug", choices=["todopoderosos", "aqui-hay-dragones"], help="default: both")
    ap.add_argument("--dry-run", action="store_true", help="query + print, don't write")
    ap.add_argument("--sample", type=int, help="process a spread of N titles")
    ap.add_argument("--limit", type=int, help="process only the first N unique titles")
    ap.add_argument("--only-missing", action="store_true", help="skip already-enriched refs when applying")
    ap.add_argument("--refresh", action="store_true", help="ignore cache, re-query")
    args = ap.parse_args()

    src = SOURCES[args.source]
    slugs = [args.slug] if args.slug else ["todopoderosos", "aqui-hay-dragones"]
    cache = load_cache()
    try:
        for slug in slugs:
            if enrich_slug(slug, args, cache, src):
                break
    finally:
        save_cache(cache)
        print(f"\ncache: {len(cache)} keys → {os.path.relpath(CACHE_PATH, ROOT)}")


if __name__ == "__main__":
    main()
