"""Henter fangstlisten for inneværende sesong fra lierelva.com og lagrer den i lierelva/data.json.

Årene 2013–2025 kommer fra Excel-arket og røres aldri. Fra 2026 erstattes årets rader med det
lierelva.com viser (siden viser hele sesongen, nyeste først). Får vi ingen rader, endres ingenting.
"""
import json, re, sys, datetime, pathlib
import requests
from bs4 import BeautifulSoup

URL = "https://www.lierelva.com/dataHeleSng.php"
FIRST_WEB_YEAR = 2026
DATA = pathlib.Path(__file__).with_name("data.json")
MAX_KG = 15.9  # høyeste valg i vektlista på lierelva.com
GEAR = {"MARK", "SPINNER", "FLUE", "SLUK"}


def fetch_rows():
    r = requests.get(URL, timeout=60, headers={"User-Agent": "Mozilla/5.0 (fangststatistikk taveisten.github.io)"})
    r.raise_for_status()
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
    rows = fetch_rows()
    print(f"Hentet {len(rows)} rader fra lierelva.com")
    if not rows:
        print("Ingen rader – lar data.json være som den er")
        return
    by_year = {}
    for row in rows:
        by_year.setdefault(int(row[0][:4]), []).append(row)
    changed = False
    for y, yr in sorted(by_year.items()):
        if y < FIRST_WEB_YEAR:
            print(f"Ignorerer {len(yr)} rader fra {y} (kommer fra Excel)")
            continue
        yr.sort(key=lambda r: (r[0], r[3]))
        old = data["years"].get(str(y), [])
        if len(yr) < len(old) * 0.8:
            print(f"{y}: bare {len(yr)} rader mot {len(old)} før – lar året være (mulig feil på kilden)")
            continue
        if yr != old:
            data["years"][str(y)] = yr
            changed = True
        data.setdefault("web", {})[str(y)] = {"rader": len(yr), "sist": yr[-1][0]}
        print(f"{y}: {len(yr)} fangster, siste {yr[-1][0]}")
    # 15,9 kg er det høyeste valget i vektlista på lierelva.com. Slike registreringer er trolig falske
    # (noen har valgt maks) og merkes som ugyldige, så de ikke telles i statistikken.
    ugyldig = data.setdefault("ugyldig", [])
    kjent = {tuple(u[:4]) for u in ugyldig}
    for y, yr in by_year.items():
        if y < FIRST_WEB_YEAR:
            continue
        for r in yr:
            if r[2] >= MAX_KG and tuple(r[:4]) not in kjent:
                ugyldig.append(r[:4] + ["Høyeste valg i vektlista (15,9 kg) – trolig falsk registrering"])
                kjent.add(tuple(r[:4]))
                changed = True
                print("Merket som ugyldig:", r)
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
