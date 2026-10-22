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


START = time.monotonic()
BUDGET = 9 * 60          # sekunder totalt; deretter brukes forrige score
FAILS = {}               # vertsnavn -> antall feil på rad


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        return json.loads(self.text)


def get(url, total=40, tries=2):
    host = requests.utils.urlparse(url).hostname
    if FAILS.get(host, 0) >= 4:
        return None
    for attempt in range(tries):
        t0 = time.monotonic()
        try:
            with S.get(url, timeout=(10, 15), stream=True) as r:
                chunks = []
                for c in r.iter_content(65536):
                    chunks.append(c)
                    if time.monotonic() - t0 > total:
                        raise requests.Timeout(f"over {total}s")
                body = b"".join(chunks).decode(r.encoding or "utf-8", "replace")
            if r.status_code == 200:
                FAILS[host] = 0
                return Resp(200, body)
            log(f"  {r.status_code} {url}")
            if r.status_code in (403, 404, 410):
                FAILS[host] = FAILS.get(host, 0) + (1 if r.status_code == 403 else 0)
                return None
        except (requests.RequestException, ValueError) as e:
            log(f"  feil {url}: {e}")
        FAILS[host] = FAILS.get(host, 0) + 1
        time.sleep(2)
    return None


def time_left():
    return BUDGET - (time.monotonic() - START)


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
            "poster": None, "shows": [], "lastAny": None,
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
            if slide.select("a.movies-screenings-button-link") and day >= today and (not f["lastAny"] or d > f["lastAny"]):
                f["lastAny"] = d   # siste visning i hele programmet, også etter 14-dagersvinduet
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


RATINGS = None


def imdb_ratings_file(wanted):
    """IMDbs offisielle datafil (oppdateres daglig): tconst, averageRating, numVotes."""
    import gzip, io
    try:
        r = S.get("https://datasets.imdbws.com/title.ratings.tsv.gz", timeout=(10, 60))
        if r.status_code != 200:
            log(f"  IMDb-datafil: {r.status_code}")
            return {}
        out = {}
        with gzip.open(io.BytesIO(r.content), "rt", encoding="utf-8") as fh:
            for line in fh:
                tt, _, rest = line.partition("\t")
                if tt in wanted:
                    rating, votes = rest.strip().split("\t")
                    out[tt] = (float(rating), int(votes))
        log(f"  IMDb-datafil: {len(out)} av {len(wanted)} filmer har rating")
        return out
    except Exception as e:
        log(f"  IMDb-datafil feilet: {e}")
        return {}


def imdb_rating(tt):
    if RATINGS and tt in RATINGS:
        rating, votes = RATINGS[tt]
        return {"id": tt, "rating": rating, "votes": votes, "genre": []}
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


# ---------------------------------------------------------------- går snart av

ENDING_DAYS = 3      # siste visning innen så mange dager …
HORIZON_GAP = 4      # … mens programmet er lagt ut minst så mange dager lenger fram


def mark_ending(films, today):
    """Merker filmer som snart slutter: siste visning snart, mens kinoen har lagt ut program lenger fram.
    Da vet vi at filmen faktisk går av, og ikke bare at resten av programmet ikke er publisert ennå."""
    horizon = max((f["lastAny"] or f["shows"][-1]["d"] for f in films), default=today.isoformat())
    for f in films:
        last = f.get("lastAny") or f["shows"][-1]["d"]
        ld = datetime.strptime(last, "%Y-%m-%d").date()
        hz = datetime.strptime(horizon, "%Y-%m-%d").date()
        f["endsSoon"] = last if (ld - today).days <= ENDING_DAYS and (hz - ld).days >= HORIZON_GAP else None


# ---------------------------------------------------------------- endringer siden forrige kjøring

def find_changes(old_data, films, today, now):
    """Hva er nytt siden forrige oppdatering: nye filmer, nye visninger, endret score, filmer tatt av programmet.
    Visninger som bare har vært, regnes ikke som endring. Er ingenting endret, beholdes forrige oppsummering."""
    old_films = {f["id"]: f for f in old_data.get("films", [])}
    prev = old_data.get("changes") or {}
    if not old_films:
        return {"at": now.isoformat(timespec="minutes"), "since": None, "first": True,
                "newFilms": [], "newShows": [], "scores": [], "gone": []}
    t0 = today.isoformat()
    new_films, new_shows, scores, gone = [], [], [], []
    for f in films:
        o = old_films.get(f["id"])
        if not o:
            new_films.append({"title": f["title"], "first": f["shows"][0]["d"] + " " + f["shows"][0]["t"], "n": len(f["shows"])})
            continue
        old_urls = {s["url"] for s in o.get("shows", [])}
        added = [s for s in f["shows"] if s["url"] not in old_urls]
        if added:
            new_shows.append({"title": f["title"], "n": len(added), "days": sorted({s["d"] for s in added})})
        diffs = []
        oi, ni = (o.get("imdb") or {}).get("rating"), (f.get("imdb") or {}).get("rating")
        if ni is not None and oi != ni:
            diffs.append(["IMDb", oi, ni])
        for key, lab in (("critics", "Kritikere"), ("audience", "Publikum")):
            ov, nv = (o.get("rt") or {}).get(key), (f.get("rt") or {}).get(key)
            if nv is not None and ov != nv:
                diffs.append([lab, ov, nv])
        if diffs:
            scores.append({"title": f["title"], "diffs": diffs})
    ids = {f["id"] for f in films}
    ended = []
    for fid, o in old_films.items():
        if fid in ids:
            continue
        last = o.get("lastAny") or max((s["d"] for s in o.get("shows", [])), default=None)
        if last and last >= t0:
            gone.append({"title": o["title"]})                 # tatt av før siste planlagte visning
        else:
            ended.append({"title": o["title"], "last": last})  # gikk sin siste visning
    ch = {"at": now.isoformat(timespec="minutes"), "since": old_data.get("updated"),
          "newFilms": new_films, "newShows": new_shows, "scores": scores, "gone": gone, "ended": ended}
    keys = ("newFilms", "newShows", "scores", "gone", "ended")
    if not any(ch[k] for k in keys) and prev and not prev.get("first") and any(prev.get(k) for k in keys):
        prev = dict(prev)
        prev["quietSince"] = now.isoformat(timespec="minutes")   # ingen endringer nå; viser forrige
        return prev
    return ch


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
    r = get(PROGRAM, total=120, tries=3)
    if not r:
        log("Fikk ikke hentet programmet – beholder forrige data.")
        sys.exit(1)
    films = parse_program(r.text, today)
    mark_ending(films, today)
    log(f"{len(films)} filmer med visninger de neste {DAYS_AHEAD} dagene")
    if not films:
        log("Ingen filmer funnet – sannsynligvis endret NF Kino siden. Beholder forrige data.")
        sys.exit(1)

    for f in films:
        prev = old.get(f["id"], {})
        if time_left() < 0:
            log("• (tid brukt opp – bruker forrige data)", f["title"])
            for k in ("genre", "orig", "director", "actors", "lang", "about", "premiere", "imdb", "rt"):
                f[k] = prev.get(k)
            continue
        log("•", f["title"], f"({int(time.monotonic() - START)} s)")
        film_details(f)
        for k in ("genre", "orig", "director", "actors", "lang", "about", "premiere"):
            if not f.get(k) and prev.get(k):
                f[k] = prev[k]
        ov = overrides.get(f["title"], {})

        # IMDb: finn id nå, rating fra datafilen etterpå
        tt = ov["imdb"] if "imdb" in ov else ((prev.get("imdb") or {}).get("id") or imdb_lookup(f, today))
        f["imdb"] = {"id": tt, "_prev": prev.get("imdb")} if tt else None

        # Rotten Tomatoes
        path = ov["rt"] if "rt" in ov else (((prev.get("rt") or {}).get("url") or "").replace(RT, "") or rt_lookup(f, today))
        f["rt"] = None
        if path:
            f["rt"] = rt_scores(path) or prev.get("rt")
        time.sleep(0.5)

    global RATINGS
    wanted = {f["imdb"]["id"] for f in films if f.get("imdb") and f["imdb"].get("id")}
    RATINGS = imdb_ratings_file(wanted)
    for f in films:
        im = f.get("imdb")
        if not im or "_prev" not in im:
            continue
        prev = im.pop("_prev")
        tt = im["id"]
        if tt in RATINGS:
            rating, votes = RATINGS[tt]
            f["imdb"] = {"id": tt, "rating": rating, "votes": votes}
        elif prev and prev.get("id") == tt and prev.get("rating") is not None:
            f["imdb"] = prev
        else:
            f["imdb"] = {"id": tt, "rating": None, "votes": None}

    films.sort(key=lambda f: (f["shows"][0]["d"], f["shows"][0]["t"], f["title"]))
    try:
        old_data = json.load(open(OUT, encoding="utf-8"))
    except Exception:
        old_data = {}
    changes = find_changes(old_data, films, today, now)
    data = {
        "updated": now.isoformat(timespec="minutes"),
        "cinema": "Drammen kino",
        "source": PROGRAM,
        "changes": changes,
        "films": films,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    n_imdb = sum(1 for f in films if f.get("imdb") and f["imdb"].get("rating"))
    n_rt = sum(1 for f in films if f.get("rt") and f["rt"].get("critics") is not None)
    log(f"Ferdig: {len(films)} filmer, {n_imdb} med IMDb-score, {n_rt} med RT-score")


if __name__ == "__main__":
    main()
