"""Engangs-sammenligning: Excel-dataene (2013–2025 i data.json) mot årssidene på lierelva.com."""
import json, pathlib, sys
from collections import Counter
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import update
D = json.loads(update.DATA.read_text(encoding="utf-8"))
ut = {}
for y in range(2013, 2026):
    update.URL = f"https://www.lierelva.com/dataHele{y}.php"
    try:
        web = update.fetch_rows()
    except Exception as e:
        ut[y] = {"feil": str(e)}; continue
    web = [r for r in web if r[0][:4] == str(y)]
    a, b = Counter(map(tuple, web)), Counter(map(tuple, D["years"].get(str(y), [])))
    ut[y] = {"web": len(web), "excel": sum(b.values()), "bare_web": [list(r) for r in (a - b).elements()], "bare_excel": [list(r) for r in (b - a).elements()]}
    print(y, len(web), sum(b.values()), len(ut[y]["bare_web"]), len(ut[y]["bare_excel"]))
pathlib.Path(__file__).with_name("sammenligning.json").write_text(json.dumps(ut, ensure_ascii=False, indent=0), encoding="utf-8")
