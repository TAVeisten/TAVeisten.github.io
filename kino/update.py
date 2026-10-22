#!/usr/bin/env python3
"""Kinoprogram Drammen – henter programmet fra NF Kino og score fra IMDb og Rotten Tomatoes.

Skriver kino/data.json, som kino/index.html viser. Kjøres av .github/workflows/kino.yml
(morgen og midt på dagen, og når «Oppdater nå» trykkes på siden).

Feil treff på IMDb eller RT rettes i kino/overrides.json:
  {"Filmtittel slik NF Kino skriver den": {"imdb": "tt1234567", "rt": "/m/slug"}}
Bruk "imdb": null eller "rt": null for å si at filmen ikke har noen score der.
"""
import json, os, re, sys, time, difflib, unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.json")
OVERRIDES = os.path.join(HERE, "overrides.json")
BASE = "https://www.nfkino.no"
PROGRAM = BASE + "/kino/drammen-kino?city=drammen"
DAYS_AHEAD = 14
OSLO = ZoneInfo("Europe/Oslo")

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
})


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, **kw):
    for attempt in range(3):
        try:
            r = S.get(url, timeout=30, **kw)
            if r.status_code == 200:
                return r
            log(f"  {r.status_code} {url}")
            if r.status_code in (403, 404):
                return None
        except requests.RequestException as e:
            log(f"  feil {url}: {e}")
        time.sleep(2 + attempt * 3)
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(\d{4}\)", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"^the |^a |^en |^et ", "", s.strip()).strip()


def minutes(txt):
    h = re.search(r"(\d+)\s*time", txt or "")
    m = re.search(r"(\d+)\s*min", txt or "")
    v = (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)
    return v or None


def clean(t):
    return re.sub(r"\s+", " ", t or "").strip()


# ---------------------------------------------------------------- NF Kino

def parse_program(html, today):
    soup = BeautifulSoup(html, "html.parser")
    last = today + timedelta(days=DAYS_AHEAD)
    films = {}
    for art in soup.select("article.node--type-movie"):
        a = art.select_one(".right-column h2.node-title a") or art.select_one("h2.node-title a")
        if not a:
            continue
        href = a.get("href", "")
        slug = re.sub(r"^/film/|\?.*$", "", href)
        title = clean(a.get_text())
        f = films.setdefault(slug, {
            "id": slug, "title": title, "url": BASE + "/film/" + slug + "?city=drammen",
            "len": minutes(clean((art.select_one(".duration") or soup.new_tag("x")).get_text())),
            "age": clean((art.select_one(".censorship") or soup.new_tag("x")).get_text()).replace("Aldersgrense:", "").strip(),
            "poster": None, "shows": [],
        })
        img = art.select_one(".movie-poster img")
        if img and img.get("src") and not f["poster"]:
            f["poster"] = BASE + img["src"] if img["src"].startswith("/") else img["src"]
        seen = {s["url"] for s in f["shows"]}
        for slide in art.select(".right-column .slide__content, .program-wrapper .slide__content"):
            tm = slide.find("time")
            if not tm or not tm.get("datetime"):
                continue
            d = tm["datetime"][:10]
            try:
                day = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < today or day > last:
                continue
            for link in slide.select("a.movies-screenings-button-link"):
                url = link.get("href")
                if not url or url in seen:
                    continue
                seen.add(url)
                btn = link.select_one(".movies-screenings-button")
                cls = btn.get("class", []) if btn else []
                avail = next((c for c in cls if c in ("many", "few", "very-few", "veryfew", "sold-out", "soldout", "full")), None)
                t = clean((link.select_one(".time") or soup.new_tag("x")).get_text()).replace(".", ":")
                tags = [clean(x) for x in clean((link.select_one(".version") or soup.new_tag("x")).get_text()).split(",") if clean(x)]
                f["shows"].append({
                    "d": d, "t": t,
                    "room": clean((link.select_one(".room") or soup.new_tag("x")).get_text()),
                    "tags": [x for x in tags if x != "2D"], "url": url, "avail": avail,
                })
    out = []
    for f in films.values():
        if f["shows"]:
            f["shows"].sort(key=lambda s: (s["d"], s["t"]))
            out.append(f)
    return out


def film_details(f):
    r = get(f["url"])
    if not r:
        return
    soup = BeautifulSoup(r.text, "html.parser")

    def field(name):
        el = soup.select_one(f".field--name-field-{name}")
        if not el:
            return ""
        items = el.select(".field__item")
        if items:
            return ", ".join(clean(i.get_text()) for i in items if clean(i.get_text()))
        lab = el.select_one(".field__label")
        txt = clean(el.get_text())
        if lab:
            txt = txt.replace(clean(lab.get_text()), "", 1).strip()
        return txt

    f["genre"] = [g.strip() for g in re.split(r",\s*", field("genre")) if g.strip() and g.strip().lower() != "unknown"]
    f["orig"] = field("original-title") or None
    f["director"] = field("directors") or None
    f["actors"] = [a for a in re.split(r",\s*", field("actors")) if a][:6]
    f["lang"] = field("language") or None
    f["about"] = (field("short-description") or "")[:300] or None
    m = re.search(r"(\d{1,2})\.?\s*(\w+)\s*(\d{4})?", field("premiere-date") or "")
    f["premiere"] = clean(field("premiere-date")) or None


# ---------------------------------------------------------------- IMDb

def name_overlap(a_list, b_text):
    b = norm(b_text)
    return sum(1 for a in a_list if a and norm(a) and norm(a) in b)


def expected_year(f, today):
    m = re.search(r"\((\d{4})\)", f["title"])
    return int(m.group(1)) if m else None


def imdb_lookup(f, today):
    ey = expected_year(f, today)
    queries = []
    for q in (f.get("orig"), re.sub(r"\s*\(\d{4}\)", "", f["title"])):
        if q and q not in queries:
            queries.append(q)
    best, best_score = None, 0
    for q in queries:
        r = get("https://v3.sg.media-imdb.com/suggestion/x/" + requests.utils.quote(q) + ".json")
        if not r:
            continue
        for c in (r.json().get("d") or []):
            if not str(c.get("id", "")).startswith("tt") or c.get("qid") not in (None, "movie", "tvMovie", "video", "tvSpecial", "musicVideo"):
                continue
            sim = max(difflib.SequenceMatcher(None, norm(c.get("l")), norm(x)).ratio() for x in queries)
            cast = name_overlap(f.get("actors", []), c.get("s", ""))
            y = c.get("y")
            score = sim * 2 + cast * 1.5
            if ey:
                score += 1.5 if y == ey else (-2 if y and abs(y - ey) > 1 else 0)
            elif y:
                score += 1 if y >= today.year - 1 else (-1.5 if y < today.year - 3 else 0)
            ok = cast >= 1 or (sim > 0.85 and (y == ey if ey else (y or 0) >= today.year - 2))
            if ok and score > best_score:
                best, best_score = c, score
        if best:
            break
    return best["id"] if best else None


def imdb_rating(tt):
    r = get(f"https://www.imdb.com/title/{tt}/")
    if not r:
        return None
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(1))
    except ValueError:
        return None
    ag = j.get("aggregateRating") or {}
    g = j.get("genre")
    return {"id": tt, "rating": ag.get("ratingValue"), "votes": ag.get("ratingCount"),
            "genre": g if isinstance(g, list) else ([g] if g else [])}


# ---------------------------------------------------------------- Rotten Tomatoes

RT = "https://www.rottentomatoes.com"


def rt_lookup(f, today):
    ey = expected_year(f, today)
    queries = []
    for q in (f.get("orig"), re.sub(r"\s*\(\d{4}\)", "", f["title"])):
        if q and q not in queries:
            queries.append(q)
    best, best_score = None, 0
    for q in queries:
        r = get(RT + "/search?search=" + requests.utils.quote(q))
        if not r:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        res = soup.select_one('search-page-result[type="movie"]') or soup
        for row in res.select("search-page-media-row"):
            a = row.select_one('a[slot="title"]') or row.select_one("a")
            if not a or "/m/" not in a.get("href", ""):
                continue
            name = clean(a.get_text())
            try:
                y = int(row.get("release-year") or 0)
            except ValueError:
                y = 0
            sim = max(difflib.SequenceMatcher(None, norm(name), norm(x)).ratio() for x in queries) if name else 0
            cast = name_overlap(f.get("actors", []), row.get("cast", ""))
            score = sim * 2 + cast * 1.5
            if ey:
                score += 1.5 if y == ey else (-2 if y and abs(y - ey) > 1 else 0)
            elif y:
                score += 1 if y >= today.year - 1 else (-1.5 if y < today.year - 3 else 0)
            ok = cast >= 1 or (sim > 0.85 and (y == ey if ey else y >= today.year - 2))
            if ok and score > best_score:
                best, best_score = a["href"].replace(RT, ""), score
        if best:
            break
    return best


def rt_scores(path):
    r = get(RT + path)
    if not r:
        return None
    m = re.search(r'<script[^>]*id="media-scorecard-json"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(1))
    except ValueError:
        return None
    c, a = j.get("criticsScore") or {}, j.get("audienceScore") or {}

    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return {"url": RT + path, "critics": num(c.get("score")), "criticsCount": c.get("reviewCount") or c.get("ratingCount"),
            "audience": num(a.get("score")), "audienceCount": a.get("bandedRatingCount")}


# ---------------------------------------------------------------- main

def main():
    now = datetime.now(OSLO)
    today = now.date()
    try:
        old = {f["id"]: f for f in json.load(open(OUT, encoding="utf-8")).get("films", [])}
    except Exception:
        old = {}
    try:
        overrides = json.load(open(OVERRIDES, encoding="utf-8"))
    except Exception:
        overrides = {}

    log("Henter program …")
    r = get(PROGRAM)
    if not r:
        log("Fikk ikke hentet programmet – beholder forrige data.")
        sys.exit(1)
    films = parse_program(r.text, today)
    log(f"{len(films)} filmer med visninger de neste {DAYS_AHEAD} dagene")
    if not films:
        log("Ingen filmer funnet – sannsynligvis endret NF Kino siden. Beholder forrige data.")
        sys.exit(1)

    for f in films:
        log("•", f["title"])
        prev = old.get(f["id"], {})
        film_details(f)
        for k in ("genre", "orig", "director", "actors", "lang", "about", "premiere"):
            if not f.get(k) and prev.get(k):
                f[k] = prev[k]
        ov = overrides.get(f["title"], {})

        # IMDb
        tt = ov["imdb"] if "imdb" in ov else ((prev.get("imdb") or {}).get("id") or imdb_lookup(f, today))
        f["imdb"] = None
        if tt:
            f["imdb"] = imdb_rating(tt) or prev.get("imdb") or {"id": tt}
        if not f.get("genre") and f["imdb"] and f["imdb"].get("genre"):
            f["genre"] = f["imdb"]["genre"]

        # Rotten Tomatoes
        path = ov["rt"] if "rt" in ov else (((prev.get("rt") or {}).get("url") or "").replace(RT, "") or rt_lookup(f, today))
        f["rt"] = None
        if path:
            f["rt"] = rt_scores(path) or prev.get("rt")
        time.sleep(0.5)

    films.sort(key=lambda f: (f["shows"][0]["d"], f["shows"][0]["t"], f["title"]))
    data = {
        "updated": now.isoformat(timespec="minutes"),
        "cinema": "Drammen kino",
        "source": PROGRAM,
        "films": films,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    n_imdb = sum(1 for f in films if f.get("imdb") and f["imdb"].get("rating"))
    n_rt = sum(1 for f in films if f.get("rt") and f["rt"].get("critics") is not None)
    log(f"Ferdig: {len(films)} filmer, {n_imdb} med IMDb-score, {n_rt} med RT-score")


if __name__ == "__main__":
    main()
