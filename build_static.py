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
from datetime import datetime, timedelta

os.environ.setdefault("RADAR_NO_BROWSER", "1")
import alerts  # noqa: E402
import private_store  # noqa: E402
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


# ---------------------------------------------------------------- Morgenbericht
WEEK_HOUR = 18                                       # Wochenrückblick sonntags ab dieser Uhrzeit
REPORT_HOUR = 6                                      # ab dieser Uhrzeit (deutsche Zeit) einmal täglich
REPORT_STATE = os.path.join(BASE, "report_state.json")
FNG_DE = {"extreme fear": "extreme Angst", "fear": "Angst", "neutral": "neutral", "greed": "Gier", "extreme greed": "extreme Gier"}


def site_url():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return None
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def pct(v):
    return f"{v * 100:+.1f} %".replace(".", ",").replace("-", "−")


def morning_report(data, now):
    m, items = data["market"], [a for a in data["items"] if a.get("d1") is not None]
    lines = []
    mood = []
    for key, label in (("crypto_fng", "Krypto"), ("stock_fng", "Aktien")):
        f = m.get(key) or {}
        if f.get("now") is not None:
            mood.append(f"{label} {f['now']} ({FNG_DE.get(str(f.get('label', '')).lower(), f.get('label', ''))})")
    if mood:
        lines.append("Stimmung (Fear & Greed): " + " · ".join(mood))
    if items:
        srt = sorted(items, key=lambda a: a["d1"])
        up = [a for a in reversed(srt) if a["d1"] > 0][:3]
        dn = [a for a in srt if a["d1"] < 0][:3]
        lines.append("")
        lines.append("Letzte Tagesbewegung:")
        if up:
            lines.append("▲ " + " · ".join(f"{a['name']} {pct(a['d1'])}" for a in up))
        if dn:
            lines.append("▼ " + " · ".join(f"{a['name']} {pct(a['d1'])}" for a in dn))
    cur = sorted(data["alerts"]["current"], key=lambda c: (c["kind"] == "info", c["prio"]))
    lines.append("")
    if cur:
        lines.append(f"Gerade auffällig ({len(cur)}):")
        for c in cur[:6]:
            lines.append(f"{alerts.ICON.get(c['kind'], '•')} {c['name']}: {c['text']}")
        if len(cur) > 6:
            lines.append(f"… und {len(cur) - 6} weitere")
    else:
        lines.append("Gerade nichts Auffälliges.")
    return f"☀️ Morgenbericht {now.strftime('%d.%m.')}", "\n".join(lines)


def tg_report(data, now):
    """Morgenbericht für Telegram: Abschnitte, Tabellen in Festbreitenschrift, Links."""
    h, m = alerts._h, data["market"]
    items = [a for a in data["items"] if a.get("d1") is not None and not a.get("error")]
    days = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    out = [f"☀️ <b>Morgenbericht</b> · {days[now.weekday()]}, {now.strftime('%d.%m.%Y')}"]

    # Stimmung
    rows = []
    for key, label in (("crypto_fng", "Krypto"), ("stock_fng", "Aktien")):
        f = m.get(key) or {}
        if f.get("now") is None:
            continue
        lab = FNG_DE.get(str(f.get("label", "")).lower(), str(f.get("label", "")))
        prev = f" (Vortag {f['d1']})" if f.get("d1") is not None else ""
        rows.append(f"{label:<7}{f['now']:>3}  {lab}{prev}")
    if rows:
        out.append("🌡 <b>Stimmung</b> · Fear &amp; Greed (0 = Panik, 100 = Gier)\n<pre>" + h("\n".join(rows)) + "</pre>")

    # Gewinner / Verlierer
    if items:
        srt = sorted(items, key=lambda a: a["d1"])
        w = max(len(a["name"]) for a in items[:]) if items else 10
        w = min(w, 14)
        line = lambda a: f"{a['name'][:w]:<{w}} {pct(a['d1']):>9}"
        up = [a for a in reversed(srt) if a["d1"] >= 0.0005][:3]
        dn = [a for a in srt if a["d1"] <= -0.0005][:3]
        if up:
            out.append("📈 <b>Gewinner</b> (letzter Handelstag)\n<pre>" + h("\n".join(map(line, up))) + "</pre>")
        if dn:
            out.append("📉 <b>Verlierer</b>\n<pre>" + h("\n".join(map(line, dn))) + "</pre>")

    # Auffälligkeiten nach Art
    cur = data["alerts"]["current"]
    if not cur:
        out.append("✅ Gerade nichts Auffälliges.")
    for kind, title in alerts.GROUPS:
        part = sorted([c for c in cur if c["kind"] == kind], key=lambda c: c["prio"])
        if not part:
            continue
        lines = [title]
        for c in part[:8]:
            lines.append(f"• <b>{alerts.tg_name(c['name'], c.get('symbol'))}</b>: {h(c['text'])}")
        if len(part) > 8:
            lines.append(f"   … und {len(part) - 8} weitere")
        out.append("\n".join(lines))

    # Bitcoin-Zyklus kurz
    b = m.get("btc") or {}
    if b.get("mvrv") is not None:
        nums = f"MVRV {b['mvrv']:.2f} · Mayer {b.get('mayer', 0):.2f}".replace(".", ",")
        out.append(f"₿ <b>Bitcoin-Zyklus</b>\n{nums} · {b.get('days', '?')} Tage seit dem Halving")
    return "\n\n".join(out)


def public_copy(data):
    """Für die öffentliche Seite: Preis-Alarme (privat) herausnehmen."""
    pub = json.loads(json.dumps(data))
    pub["alerts"]["current"] = [c for c in pub["alerts"]["current"] if c["kind"] != "alarm"]
    pub["alerts"]["log"] = [e for e in pub["alerts"]["log"] if e["kind"] != "alarm"]
    return pub


def week_log(now):
    """Meldungen der letzten 7 Tage (Zeitformat 'TT.MM. HH:MM')."""
    days = {(now - timedelta(days=i)).strftime("%d.%m.") for i in range(7)}
    return [e for e in alerts.log() if e.get("time", "")[:6] in days]


def send_image(kind, data, now, depot, caption, title, fallback=None):
    """Privates Bild (mit Depot) erzeugen und über Telegram/ntfy schicken."""
    import report_image
    img = os.path.join(BASE, ".cache", f"{kind}.png")   # NICHT in site/ – enthält das Depot
    os.makedirs(os.path.dirname(img), exist_ok=True)
    report_image.render(data, now, img, depot=depot if depot and depot["rows"] else None,
                        week={"log": week_log(now)} if kind == "week" else None)
    if os.environ.get("RADAR_TEST") == "1":
        print(f"--- {title} (Vorschau, nicht gesendet) -> {img}\n{caption}")
        return True
    sent = []
    if alerts.telegram_config()[1]:
        ok = alerts.send_telegram_photo(img, caption)
        sent.append(("Telegram", ok or (bool(fallback) and alerts.send_telegram(fallback))))
    if alerts.ntfy_topic():
        sent.append(("ntfy", alerts.send_ntfy_image(img, title, "Tippen öffnet den Radar.", click=site_url())))
    print(f"{title}:", ", ".join(f"{k} {'ok' if v else 'FEHLER'}" for k, v in sent) or "kein Kanal eingerichtet")
    return any(v for _, v in sent)


def depot_line(depot, week=False):
    if not depot or not depot["rows"]:
        return ""
    per, pp = (depot["week"], depot["week_pct"]) if week else (depot["day"], depot["day_pct"])
    sign = lambda v: ("+" if v >= 0 else "−") + alerts.fmt_num(abs(v))
    return (f"\n💼 Depot {alerts.fmt_num(depot['value'])} $ · {'Woche' if week else 'heute'} {sign(per)} $"
            + (f" ({alerts.pct(pp)})" if pp is not None else ""))


def maybe_send_reports(data, now, depot, force=False):
    force = force or os.environ.get("FORCE_REPORT") == "1"
    st = json.load(open(REPORT_STATE)) if os.path.exists(REPORT_STATE) else {}
    today, wk = now.strftime("%Y-%m-%d"), now.strftime("%G-W%V")
    cur = data["alerts"]["current"]
    n = {k: sum(1 for c in cur if c["kind"] == k) for k in ("alarm", "good", "bad", "info")}

    if force or (now.hour >= REPORT_HOUR and st.get("last") != today):
        caption = (f"☀️ <b>Morgenbericht</b> · {now.strftime('%d.%m.%Y')}\n"
                   + (f"🔔 {n['alarm']} Preis-Alarm · " if n["alarm"] else "")
                   + f"🟢 {n['good']} Kaufsignal{'e' if n['good'] != 1 else ''} · 🔴 {n['bad']} Vorsicht"
                   + depot_line(depot))
        ok = send_image("day", data, now, depot, caption, f"☀️ Morgenbericht {now.strftime('%d.%m.')}",
                        fallback=tg_report(data, now))
        if ok and not force:
            st["last"] = today

    # Wochenrückblick: sonntags ab 18 Uhr
    if (now.weekday() == 6 and now.hour >= WEEK_HOUR and st.get("last_week") != wk) or os.environ.get("FORCE_WEEK") == "1":
        best = sorted([a for a in data["items"] if a.get("d7") is not None], key=lambda a: -a["d7"])
        caption = (f"📅 <b>Wochenrückblick</b> · KW {now.strftime('%V')}\n"
                   + (f"Bester: {alerts._h(best[0]['name'])} {alerts.pct(best[0]['d7'])} · "
                      f"Schwächster: {alerts._h(best[-1]['name'])} {alerts.pct(best[-1]['d7'])}" if best else "")
                   + depot_line(depot, week=True))
        ok = send_image("week", data, now, depot, caption, f"📅 Wochenrückblick KW {now.strftime('%V')}")
        if ok and os.environ.get("FORCE_WEEK") != "1":
            st["last_week"] = wk
    if os.environ.get("RADAR_TEST") != "1":
        with open(REPORT_STATE, "w") as f:
            json.dump(st, f)


def main():
    t0 = time.time()
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    shutil.copytree(os.path.join(BASE, "static"), os.path.join(OUT, "static"))
    os.makedirs(BT_CACHE, exist_ok=True)

    client = app.app.test_client()

    # 1) Übersicht + Auffälligkeiten (schickt dabei neue Meldungen)
    data = json.loads(get(client, "/api/data"))
    now = datetime.now()
    data["built"] = now.strftime("%d.%m. %H:%M")
    data["built_ts"] = int(time.time())
    pub = public_copy(data)
    write("data.json", pub)
    print(f"Übersicht: {len(data['items'])} Werte, {len(data['alerts']['current'])} gerade auffällig")

    print(f"  … {time.time() - t0:.0f} s")
    # 2) Telegram-Befehle beantworten (/kurs, /add, /alarm, /kauf …)
    import telegram_bot
    changed = telegram_bot.process(data, client)
    with open(os.path.join(BASE, ".cache", "changed.json"), "w") as f:
        json.dump(changed, f)

    print(f"  … {time.time() - t0:.0f} s")
    # 3) Berichte: öffentliches Bild (ohne Depot) für die Seite, privates zum Verschicken
    priv = private_store.load()
    depot = private_store.summary(priv["depot"], data["items"])
    try:
        import report_image
        report_image.render(pub, now, os.path.join(OUT, "data", "report.png"))
    except Exception as e:
        print("Berichtsbild fehlgeschlagen:", e)
    try:
        maybe_send_reports(data, now, depot, force=changed.get("report"))
    except Exception as e:
        print("Bericht senden fehlgeschlagen:", e)

    wl = app.load_wl()
    print(f"  … {time.time() - t0:.0f} s")
    # 4) Detailseiten + News je Wert
    for w in wl:
        s = w["symbol"]
        write(f"asset/{fn(s)}.json", get(client, f"/api/asset?symbol={s}"))
        for lang in LANGS:
            write(f"news/{fn(s)}-{lang}.json", get(client, f"/api/news?symbol={s}&lang={lang}"))

    print(f"  … {time.time() - t0:.0f} s")
    # 5) Strategie-Vergleich (rechenintensiv -> nur ~1x am Tag neu)
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

    print(f"  … {time.time() - t0:.0f} s")
    # 6) Seite: Pfade relativ machen (GitHub Pages liegt unter /<repo>/) und Cloud-Modus einschalten
    html = open(os.path.join(BASE, "static", "index.html"), encoding="utf-8").read()
    html = html.replace('"/static/', '"static/').replace('href="/manifest.webmanifest"', 'href="manifest.webmanifest"')
    html = html.replace("<head>", "<head>\n<script>window.RADAR_STATIC = true;</script>", 1)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    manifest = {"name": "MarketScan", "short_name": "MarketScan", "start_url": "./", "display": "standalone",
                "background_color": "#0f1115", "theme_color": "#0f1115",
                "icons": [{"src": "static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "static/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
    with open(os.path.join(OUT, "manifest.webmanifest"), "w") as f:
        json.dump(manifest, f)
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    print(f"Fertig in {time.time() - t0:.0f} s -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
