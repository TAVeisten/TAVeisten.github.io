"""Henter alle fangster fra lierelva.com og lagrer dem i lierelva/data.json.

Hvert år hentes fra årssiden (dataHele<år>.php, 2013 og fram til i fjor), og inneværende sesong fra
dataHeleSng.php. Et år erstattes med det lierelva.com viser. Får et år ingen rader, eller plutselig
mye færre enn før, blir det stående uendret.
"""
import json, os, re, sys, time, datetime, pathlib
from collections import Counter
import requests
from bs4 import BeautifulSoup

URL = "https://www.lierelva.com/dataHeleSng.php"          # inneværende sesong
ARKIV = "https://www.lierelva.com/dataHele{}.php"        # tidligere år
FIRST_YEAR = 2013
DATA = pathlib.Path(__file__).with_name("data.json")
MAX_KG = 15.9  # høyeste valg i vektlista på lierelva.com
GEAR = {"MARK", "SPINNER", "FLUE", "SLUK"}


def get(url):
    """Henter en side, med inntil tre forsøk hvis lierelva.com svarer tregt eller med feil."""
    for i in range(3):
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (fangststatistikk taveisten.github.io)"})
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"Forsøk {i + 1} feilet for {url}: {e}")
            if i == 2:
                raise
            time.sleep(20)


def fetch_rows(url=URL):
    r = get(url)
    r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
    soup = BeautifulSoup(r.text, "html.parser")
    rows, skipped = [], []
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 5:
            continue
        m = re.fullmatch(r"(\d{1,2})[-.](\d{1,2})[-.](\d{4})", cells[0])
        if not m:
            continue
        fish = cells[1].upper()
        gear = cells[4].upper()
        try:
            kg = round(float(cells[2].replace(",", ".")), 1)
        except ValueError:
            skipped.append(cells); continue
        if fish.startswith("LAKS"):
            f = "L"
        elif fish.startswith("SJ"):
            f = "S"
        else:
            skipped.append(cells); continue
        if gear not in GEAR:
            skipped.append(cells); continue
        d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        rows.append([d, f, kg, re.sub(r"\s+", " ", cells[3]).strip(), gear])
    for s in skipped:
        print("Hoppet over rad:", s)
    return rows


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    by_year = {}
    this_year = datetime.date.today().year
    for y in range(FIRST_YEAR, this_year + 1):
        url = ARKIV.format(y)
        try:
            rows = [r for r in fetch_rows(url) if int(r[0][:4]) == y]
        except Exception as e:
            print(f"{y}: kunne ikke hentes ({e})"); rows = []
        if rows:
            by_year[y] = rows
    # Inneværende sesong: brukes når årssiden ikke finnes ennå, eller har færre rader enn sesongsiden
    try:
        sesong = fetch_rows(URL)
        for y in {int(r[0][:4]) for r in sesong}:
            s = [r for r in sesong if int(r[0][:4]) == y]
            if len(s) >= len(by_year.get(y, [])):
                by_year[y] = s
    except Exception as e:
        print(f"Sesongsiden kunne ikke hentes ({e})")
    by_year = {y: v for y, v in by_year.items() if v}
    print("Hentet:", {y: len(v) for y, v in sorted(by_year.items())})
    if not by_year:
        print("Ingen rader – lar data.json være som den er")
        return
    changed = False
    lagt_til, fjernet = [], []
    for y, yr in sorted(by_year.items()):
        yr.sort(key=lambda r: (r[0], r[3]))
        old = data["years"].get(str(y), [])
        if len(yr) < len(old) * 0.8:
            print(f"{y}: bare {len(yr)} rader mot {len(old)} før – lar året være (mulig feil på kilden)")
            continue
        if yr != old:
            a, b = Counter(map(tuple, yr)), Counter(map(tuple, old))
            lagt_til += [list(r) for r in (a - b).elements()]
            fjernet += [list(r) for r in (b - a).elements()]
            data["years"][str(y)] = yr
            changed = True
        data.setdefault("web", {})[str(y)] = {"rader": len(yr), "sist": yr[-1][0]}
        print(f"{y}: {len(yr)} fangster, siste {yr[-1][0]}")
    # 15,9 kg er det høyeste valget i vektlista på lierelva.com, så slike registreringer kan være falske
    # (noen har valgt maks). De telles med som vanlig, men merkes som mistenkelige: siden viser en melding,
    # og GitHub-jobben oppretter en sak (issue) slik at Thom Are får beskjed og kan avgjøre om den skal tas ut.
    mist = data.setdefault("mistenkelig", [])
    kjent = {tuple(u[:4]) for k in ("mistenkelig", "ugyldig", "godkjent") for u in data.get(k, [])}
    nye = []
    for y, yr in by_year.items():
        for r in yr:
            if r[2] >= MAX_KG and tuple(r[:4]) not in kjent:
                mist.append(r[:4] + ["Høyeste valg i vektlista (15,9 kg) – mulig falsk registrering"])
                kjent.add(tuple(r[:4]))
                nye.append(r)
                changed = True
                print("Mistenkelig registrering:", r)
    if nye and os.environ.get("GITHUB_OUTPUT"):
        linjer = "\n".join(f"- {r[0]}: {'laks' if r[1] == 'L' else 'sjøørret'} {r[2]} kg, {r[3]}, {r[4].lower()}" for r in nye)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write("mistenkelig<<EOF\n" + linjer + "\nEOF\n")
    # «Nytt siden sist»: skrives bare når noe er endret, ellers står forrige oppsummering igjen
    if lagt_til or fjernet or nye:
        def art(r): return "laks" if r[1] == "L" else "sjøørret"
        def kg(v): return f"{v:.1f}".replace(".", ",")
        pkt = []
        if lagt_til:
            nl = sum(1 for r in lagt_til if r[1] == "L"); ns = len(lagt_til) - nl
            st = max(lagt_til, key=lambda r: r[2])
            pkt.append(f"{len(lagt_til)} {'ny fangst' if len(lagt_til) == 1 else 'nye fangster'} ({nl} laks, {ns} sjøørret), største {kg(st[2])} kg {art(st)}.")
        if fjernet:
            pkt.append(f"{len(fjernet)} {'registrering' if len(fjernet) == 1 else 'registreringer'} fjernet fra lierelva.com.")
        for r in nye:
            pkt.append(f"Mulig falsk registrering: {kg(r[2])} kg {art(r)} {r[0][8:10]}.{r[0][5:7]}.{r[0][:4]} ({r[3].title()}). 15,9 kg er høyeste valg i vektlista – telles med inntil du sier fra.")
        data["nytt"] = {"tid": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes"),
                        "punkter": pkt, "nye": sorted(lagt_til, key=lambda r: (r[0], r[2]), reverse=True)[:50]}
    data["checked"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")
    if changed:
        data["updated"] = data["checked"]
    DATA.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("FEIL:", e)
        sys.exit(1)
