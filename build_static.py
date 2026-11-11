"""Baut den Radar als fertige Webseite (für GitHub Pages).

Läuft in der Cloud (GitHub Actions) alle ~15 Minuten:
  1. holt alle Daten genau wie der Radar auf dem Mac (gleicher Code, app.py),
  2. prüft Auffälligkeiten und schickt neue Meldungen per ntfy aufs Handy,
  3. schreibt alles als Dateien nach  site/  – die Webseite liest nur noch diese Dateien.

Lokal ausprobieren:  .venv/bin/python build_static.py  &&  python3 -m http.server -d site 8060
"""
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

os.environ.setdefault("RADAR_NO_BROWSER", "1")
import alerts  # noqa: E402
import app  # noqa: E402  (nutzt dieselben Funktionen wie der Mac-Radar)

if os.environ.get("RADAR_TEST") == "1":
    # Probelauf am Mac: keine echten Meldungen, eigener Merkzettel statt dem des Mac-Radars
    _d = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "test")
    os.makedirs(_d, exist_ok=True)
    alerts.STATE_FILE, alerts.LOG_FILE = os.path.join(_d, "state.json"), os.path.join(_d, "log.json")
    alerts.send_ntfy_entries = alerts.notify = alerts.send_telegram = lambda *a, **k: None

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "site")
BT_CACHE = os.path.join(BASE, ".cache", "backtest")   # wird zwischen Läufen aufbewahrt
BT_MAX_AGE = 20 * 3600                                # Strategie-Vergleich ~1x täglich neu rechnen
LANGS = ("de", "en")


def fn(sym):
    """Symbol -> sicherer Dateiname (gleiche Regel wie im Browser)."""
    return re.sub(r"[^A-Za-z0-9._-]", lambda m: "_%02X" % ord(m.group()), sym)


def write(rel, data):
    path = os.path.join(OUT, "data", rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def get(client, url):
    r = client.get(url)
    return r.get_data(as_text=True)


def main():
    t0 = time.time()
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    shutil.copytree(os.path.join(BASE, "static"), os.path.join(OUT, "static"))
    os.makedirs(BT_CACHE, exist_ok=True)

    client = app.app.test_client()
    wl = app.load_wl()

    # 1) Übersicht + Auffälligkeiten (schickt dabei neue Meldungen per ntfy)
    data = json.loads(get(client, "/api/data"))
    now = datetime.now()
    data["built"] = now.strftime("%d.%m. %H:%M")
    data["built_ts"] = int(time.time())
    write("data.json", data)
    print(f"Übersicht: {len(data['items'])} Werte, {len(data['alerts']['current'])} gerade auffällig")

    # 2) Detailseiten + News je Wert
    for w in wl:
        s = w["symbol"]
        write(f"asset/{fn(s)}.json", get(client, f"/api/asset?symbol={s}"))
        for lang in LANGS:
            write(f"news/{fn(s)}-{lang}.json", get(client, f"/api/news?symbol={s}&lang={lang}"))

    # 3) Strategie-Vergleich (rechenintensiv -> nur ~1x am Tag neu)
    for w in wl:
        s = w["symbol"]
        cached = os.path.join(BT_CACHE, fn(s) + ".json")
        if not os.path.exists(cached) or time.time() - os.path.getmtime(cached) > BT_MAX_AGE:
            res = get(client, f"/api/backtest?symbol={s}")
            if '"error"' not in res[:40]:
                with open(cached, "w", encoding="utf-8") as f:
                    f.write(res)
            else:
                write(f"backtest/{fn(s)}.json", res)
                continue
        with open(cached, encoding="utf-8") as f:
            write(f"backtest/{fn(s)}.json", f.read())

    # 4) Seite: Pfade relativ machen (GitHub Pages liegt unter /<repo>/) und Cloud-Modus einschalten
    html = open(os.path.join(BASE, "static", "index.html"), encoding="utf-8").read()
    html = html.replace('"/static/', '"static/').replace('href="/manifest.webmanifest"', 'href="manifest.webmanifest"')
    html = html.replace("<head>", "<head>\n<script>window.RADAR_STATIC = true;</script>", 1)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    manifest = {"name": "Markt-Radar", "short_name": "Radar", "start_url": "./", "display": "standalone",
                "background_color": "#0f1115", "theme_color": "#0f1115",
                "icons": [{"src": "static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "static/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
    with open(os.path.join(OUT, "manifest.webmanifest"), "w") as f:
        json.dump(manifest, f)
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    print(f"Fertig in {time.time() - t0:.0f} s -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
