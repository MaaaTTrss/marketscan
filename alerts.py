"""Auffälligkeiten erkennen und als Mac-Benachrichtigung melden.

Eine Regel ist entweder "aktiv" (z. B. RSI unter 30) oder nicht. Benachrichtigt wird
nur, wenn eine Regel NEU aktiv wird – so bekommst du nicht ständig dieselbe Meldung.
Schwellen kannst du unten in RULES anpassen.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

import requests
from dotenv import dotenv_values, set_key

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "alerts_state.json")
LOG_FILE = os.path.join(BASE, "alerts_log.json")
SETTINGS_FILE = os.path.join(BASE, "settings.json")
ENV_FILE = os.path.join(BASE, ".env")

# Schwellen
RSI_LOW, RSI_HIGH = 30, 70
DAY_MOVE = {"crypto": 0.08, "stock": 0.05, "etf": 0.03}   # Tagesbewegung, ab der gemeldet wird
DRAWDOWN_LEVELS = [-0.3, -0.5, -0.7]                      # Abstand zum Allzeithoch
FNG_LOW, FNG_HIGH = 20, 80


def pct(v):
    return f"{v * 100:+.1f} %".replace(".", ",")


WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def fmt_num(v):
    d = 0 if v >= 1000 else 2 if v >= 1 else 4
    return f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def quiet(key):
    """Dauer-Hinweise (Abstand zum Allzeithoch) nur im Radar zeigen, nicht melden."""
    return ":dd" in key or key.endswith(":ath")


def conditions(snapshot, private=None):
    """Alle gerade zutreffenden Auffälligkeiten.
    Rückgabe: Liste von dicts {key, symbol, name, kind(good/bad/info/alarm), text, prio}
    private = {"alarms": [...]} (Preis-Alarme, nur am Mac / in der Cloud mit Schlüssel)"""
    out = []

    def add(key, sym, name, kind, text, prio):
        out.append({"key": key, "symbol": sym, "name": name, "kind": kind, "text": text, "prio": prio})

    for a in snapshot["items"]:
        if a.get("error") or a.get("price") is None:
            continue
        s, n, g = a["symbol"], a["name"], a.get("group", "stock")
        if a.get("rsi") is not None:
            r, tr = a["rsi"], a.get("trend")
            # RSI zusammen mit dem Trend: überverkauft im Aufwärtstrend ist ein deutlich besseres Zeichen
            # als im Abwärtstrend (dort fällt es oft einfach weiter).
            if r < RSI_LOW:
                if tr == "auf":
                    add(f"{s}:rsi_low", s, n, "good", f"★ RSI {r:.0f} – überverkauft im Aufwärtstrend", 1)
                elif tr == "ab":
                    add(f"{s}:rsi_low", s, n, "info", f"RSI {r:.0f} – überverkauft, aber Abwärtstrend", 3)
                else:
                    add(f"{s}:rsi_low", s, n, "good", f"RSI {r:.0f} – überverkauft", 2)
            elif r > RSI_HIGH:
                if tr == "ab":
                    add(f"{s}:rsi_high", s, n, "bad", f"★ RSI {r:.0f} – überkauft im Abwärtstrend", 1)
                elif tr == "auf":
                    add(f"{s}:rsi_high", s, n, "info", f"RSI {r:.0f} – überkauft, Aufwärtstrend läuft", 3)
                else:
                    add(f"{s}:rsi_high", s, n, "bad", f"RSI {r:.0f} – überkauft", 2)
        if a.get("earnings"):
            try:
                ed = datetime.strptime(a["earnings"], "%Y-%m-%d").date()
                days = (ed - datetime.now().date()).days
                if 0 <= days <= 2:
                    when = "heute" if days == 0 else "morgen" if days == 1 else f"am {WD[ed.weekday()]} {ed.strftime('%d.%m.')}"
                    add(f"{s}:earn{a['earnings']}", s, n, "info", f"Quartalszahlen {when}", 2)
            except ValueError:
                pass
        if a.get("trend") == "auf":
            add(f"{s}:trend_auf", s, n, "good", "Trendwechsel: jetzt Aufwärts", 0)
        elif a.get("trend") == "ab":
            add(f"{s}:trend_ab", s, n, "bad", "Trendwechsel: jetzt Abwärts", 0)
        d1, lim = a.get("d1"), DAY_MOVE.get(g, 0.05)
        if d1 is not None and abs(d1) >= lim:
            add(f"{s}:move_{'up' if d1 > 0 else 'down'}", s, n, "info",
                f"Starke Tagesbewegung: {pct(d1)}", 3)
        if a.get("dd") is not None:
            if a["dd"] >= -0.005:
                add(f"{s}:ath", s, n, "info", "Auf/nahe Allzeithoch", 3)
            hit = [lvl for lvl in DRAWDOWN_LEVELS if a["dd"] <= lvl]
            if hit:
                add(f"{s}:dd{int(min(hit) * 100)}", s, n, "info",
                    f"{abs(a['dd']) * 100:.0f} % unter dem Allzeithoch", 1)

    m = snapshot.get("market") or {}
    for key, label in (("crypto_fng", "Krypto"), ("stock_fng", "Aktien")):
        f = m.get(key)
        if f and f["now"] <= FNG_LOW:
            add(f"{key}:low", "", f"{label}-Stimmung", "good", f"Extreme Angst ({f['now']})", 3)
        elif f and f["now"] >= FNG_HIGH:
            add(f"{key}:high", "", f"{label}-Stimmung", "bad", f"Extreme Gier ({f['now']})", 3)
    b = m.get("btc")
    if b:
        if b["mvrv"] < 1:
            add("btc:mvrv_low", "BTC-USD", "Bitcoin", "good", f"MVRV {b['mvrv']:.2f} – unter 1", 3)
        elif b["mvrv"] > 3:
            add("btc:mvrv_high", "BTC-USD", "Bitcoin", "bad", f"MVRV {b['mvrv']:.2f} – über 3", 3)
        if b["mayer"] < 0.8:
            add("btc:mayer_low", "BTC-USD", "Bitcoin", "good", f"Mayer-Multiple {b['mayer']:.2f} – günstig", 2)
        elif b["mayer"] > 2.4:
            add("btc:mayer_high", "BTC-USD", "Bitcoin", "bad", f"Mayer-Multiple {b['mayer']:.2f} – überhitzt", 2)
    # Preis-Alarme
    prices = {a["symbol"]: a for a in snapshot["items"] if a.get("price") is not None}
    for al in (private or {}).get("alarms", []):
        a = prices.get(al["symbol"])
        if not a:
            continue
        hit = a["price"] <= al["price"] if al["op"] == "below" else a["price"] >= al["price"]
        if hit:
            word = "unter" if al["op"] == "below" else "über"
            add(f"alarm:{al['id']}", al["symbol"], al["name"], "alarm",
                f"Preis-Alarm: {word} {fmt_num(al['price'])} $ (jetzt {fmt_num(a['price'])} $)", 1)
    out.sort(key=lambda c: -c["prio"])
    return out


# ---------------------------------------------------------------- Zustand & Verlauf
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def settings():
    return {"mac_notifications": True, "telegram_notifications": True, "ntfy_notifications": True, **_load(SETTINGS_FILE, {})}


def save_settings(s):
    _save(SETTINGS_FILE, s)


def log():
    return _load(LOG_FILE, [])


def check(snapshot, private=None):
    """Vergleicht mit dem letzten Stand, meldet Neues. Gibt die neuen Meldungen zurück."""
    conds = conditions(snapshot, private)
    state = _load(STATE_FILE, None)
    symbols = [a["symbol"] for a in snapshot["items"]] + [""]
    keys = {c["key"] for c in conds}
    if state is None:  # allererster Lauf: nur merken, nichts melden
        _save(STATE_FILE, {"active": sorted(keys), "symbols": symbols})
        return []
    known = set(state.get("symbols", []))
    prev = set(state.get("active", []))
    # Neu hinzugefügte Werte nicht sofort mit allen Zuständen melden
    new = [c for c in conds if c["key"] not in prev and not quiet(c["key"])
           and (c["symbol"] in known or c["key"].startswith(("btc:", "alarm:")))]
    _save(STATE_FILE, {"active": sorted(keys), "symbols": symbols})
    if not new:
        return []
    now = datetime.now().strftime("%d.%m. %H:%M")
    entries = [{**c, "time": now} for c in new]
    _save(LOG_FILE, (entries + log())[:150])
    st = settings()
    if st.get("mac_notifications"):
        notify(entries)
    if st.get("ntfy_notifications") and ntfy_topic():
        send_ntfy_entries(entries)
    if st.get("telegram_notifications") and telegram_config()[1]:
        send_telegram(format_telegram(entries, snapshot))
    return entries


def notify(entries):
    if len(entries) == 1:
        e = entries[0]
        title, text = f"MarketScan: {e['name']}", e["text"]
    else:
        title = f"MarketScan: {len(entries)} neue Auffälligkeiten"
        text = " · ".join(f"{e['name']}: {e['text']}" for e in entries[:4])
    send_mac(title, text)


def send_mac(title, text):
    if sys.platform != "darwin":   # z. B. in der Cloud (GitHub) gibt es keine Mac-Mitteilungen
        return
    try:
        subprocess.run(["osascript", "-e", "on run argv",
                        "-e", 'display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"',
                        "-e", "end run", title, text], timeout=10, check=False)
    except Exception:
        pass


# ---------------------------------------------------------------- Telegram (Handy)
TG = "https://api.telegram.org/bot{token}/{method}"
ICON = {"good": "🟢", "bad": "🔴", "info": "🔵", "alarm": "🔔"}


def telegram_config():
    env = dotenv_values(ENV_FILE) if os.path.exists(ENV_FILE) else {}
    # In der Cloud kommen beide Werte aus GitHub-Secrets
    return (env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN"),
            env.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID"))


def site_url():
    """Adresse der Cloud-Seite (für Links in den Nachrichten)."""
    url = os.environ.get("RADAR_SITE_URL")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not url and "/" in repo:
        owner, name = repo.split("/", 1)
        url = f"https://{owner.lower()}.github.io/{name}/"
    return url


def _save_env(key, value):
    if not os.path.exists(ENV_FILE):
        open(ENV_FILE, "w").close()
    set_key(ENV_FILE, key, value, quote_mode="never")


def telegram_call(method, token=None, **params):
    token = token or telegram_config()[0]
    if not token:
        raise RuntimeError("Kein Bot-Token gespeichert.")
    r = requests.post(TG.format(token=token, method=method), json=params, timeout=15).json()
    if not r.get("ok"):
        raise RuntimeError(r.get("description", "Telegram-Fehler"))
    return r["result"]


def telegram_set_token(token):
    # Akzeptiert auch die ganze BotFather-Nachricht und fischt den Token heraus
    import re
    m = re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", token or "")
    if not m:
        raise RuntimeError("Kein Token erkannt. Er sieht so aus: 1234567890:AAH…, ca. 45 Zeichen.")
    token = m.group(0)
    me = telegram_call("getMe", token=token)
    _save_env("TELEGRAM_BOT_TOKEN", token)
    return me["username"]


def telegram_find_chat():
    """Nimmt den Chat der letzten Nachricht an den Bot (nachdem du dort auf Start gedrückt hast)."""
    updates = telegram_call("getUpdates")
    chats = [u["message"]["chat"] for u in updates if "message" in u]
    if not chats:
        raise RuntimeError("Noch keine Nachricht gefunden. Öffne deinen Bot in Telegram, "
                           "drück auf „Start“ und versuch es dann nochmal.")
    chat = chats[-1]
    _save_env("TELEGRAM_CHAT_ID", str(chat["id"]))
    return chat.get("first_name") or chat.get("title") or str(chat["id"])


GROUPS = [("alarm", "🔔 <b>Preis-Alarm</b>"), ("good", "🟢 <b>Spricht eher für Kauf</b>"), ("bad", "🔴 <b>Eher Vorsicht</b>"), ("info", "🔵 <b>Zur Info</b>")]


def fmt_price(v, cur="USD"):
    if v is None:
        return ""
    d = 2 if v < 1000 else 0
    if v < 1:
        d = 4
    s = f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} {'$' if cur == 'USD' else '€' if cur == 'EUR' else cur}"


def tg_name(name, symbol):
    """Name als Link auf die Einzelanalyse in der Cloud-Seite (falls bekannt)."""
    url = site_url()
    if url and symbol:
        from urllib.parse import quote
        return f'<a href="{url}#/wert/{quote(symbol)}">{_h(name)}</a>'
    return _h(name)


def format_telegram(entries, snapshot=None):
    """Übersichtliche Telegram-Nachricht: nach Art gruppiert, mit Kurs und Tagesänderung."""
    info = {a["symbol"]: a for a in (snapshot or {}).get("items", [])}
    when = datetime.now().strftime("%d.%m. %H:%M")
    n = len(entries)
    head = f"📡 <b>MarketScan</b> · {n} neue Auffälligkeit{'en' if n != 1 else ''}\n<i>{when} Uhr</i>"
    blocks = [head]
    for kind, title in GROUPS:
        part = [e for e in entries if e["kind"] == kind]
        if not part:
            continue
        lines = [title]
        for e in part[:10]:
            lines.append(f"• <b>{tg_name(e['name'], e.get('symbol'))}</b>: {_h(e['text'])}")
            a = info.get(e.get("symbol"))
            if a and a.get("price") is not None:
                d1 = a.get("d1")
                extra = f" · heute {pct(d1)}" if d1 is not None else ""
                lines.append(f"   <i>{fmt_price(a['price'], a.get('currency', 'USD'))}{extra}</i>")
        if len(part) > 10:
            lines.append(f"   … und {len(part) - 10} weitere")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _h(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram(text, button=True):
    token, chat = telegram_config()
    if not (token and chat):
        return False
    extra = {}
    if button and site_url():
        extra["reply_markup"] = {"inline_keyboard": [[{"text": "📊 MarketScan öffnen", "url": site_url()}]]}
    try:
        telegram_call("sendMessage", token=token, chat_id=chat, text=text,
                      parse_mode="HTML", disable_web_page_preview=True, **extra)
        return True
    except Exception as e:
        print("Telegram-Versand fehlgeschlagen:", e)
        return False


def send_telegram_photo(path, caption):
    """Bild (z. B. Morgenbericht) mit kurzer Bildunterschrift und Knopf schicken."""
    token, chat = telegram_config()
    if not (token and chat):
        return False
    data = {"chat_id": chat, "caption": caption[:1024], "parse_mode": "HTML"}
    if site_url():
        data["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": "📊 MarketScan öffnen", "url": site_url()}]]})
    try:
        with open(path, "rb") as f:
            r = requests.post(TG.format(token=token, method="sendPhoto"), data=data,
                              files={"photo": ("morgenbericht.png", f, "image/png")}, timeout=30).json()
        if not r.get("ok"):
            raise RuntimeError(r.get("description"))
        return True
    except Exception as e:
        print("Telegram-Bild fehlgeschlagen:", e)
        return False


# ---------------------------------------------------------------- ntfy (Handy, ohne Konto)
NTFY_SERVER = "https://ntfy.sh"
_ALPHA = "abcdefghjkmnpqrstuvwxyz23456789"   # ohne verwechselbare Zeichen (0/o, 1/l/i)


def ntfy_topic():
    env = dotenv_values(ENV_FILE) if os.path.exists(ENV_FILE) else {}
    return env.get("NTFY_TOPIC") or os.environ.get("NTFY_TOPIC")   # Cloud: kommt aus einem GitHub-Secret


def ntfy_new_topic():
    """Zufälliger, nicht erratbarer Kanalname, z. B. radar-k7m2-x9qp-4hfd."""
    import secrets
    parts = ["".join(secrets.choice(_ALPHA) for _ in range(4)) for _ in range(3)]
    topic = "radar-" + "-".join(parts)
    _save_env("NTFY_TOPIC", topic)
    return topic


def send_ntfy(title, message, tags=None, priority=3, click=None):
    topic = ntfy_topic()
    if not topic:
        return False
    body = {"topic": topic, "title": title, "message": message, "tags": tags or [], "priority": priority}
    if click:
        body["click"] = click   # Tippen auf die Meldung öffnet diese Seite
    try:
        r = requests.post(NTFY_SERVER, timeout=15, json=body)
        return r.ok
    except Exception as e:
        print("ntfy-Versand fehlgeschlagen:", e)
        return False


def send_ntfy_image(path, title, message, click=None):
    """Bild als Anhang über ntfy (Titel/Text als URL-Parameter, damit Umlaute/Emojis gehen)."""
    topic = ntfy_topic()
    if not topic:
        return False
    params = {"title": title, "message": message, "filename": "morgenbericht.png", "tags": "sunny"}
    if click:
        params["click"] = click
    try:
        with open(path, "rb") as f:
            r = requests.put(f"{NTFY_SERVER}/{topic}", params=params, data=f, timeout=30)
        return r.ok
    except Exception as e:
        print("ntfy-Bild fehlgeschlagen:", e)
        return False


def send_ntfy_entries(entries):
    if len(entries) == 1:
        e = entries[0]
        title, msg = f"MarketScan: {e['name']}", f"{ICON.get(e['kind'], '•')} {e['text']}"
    else:
        title = f"MarketScan: {len(entries)} neue Auffälligkeiten"
        msg = "\n".join(f"{ICON.get(e['kind'], '•')} {e['name']}: {e['text']}" for e in entries[:12])
    important = any(e.get("prio", 0) >= 3 for e in entries)
    return send_ntfy(title, msg, tags=["chart_with_upwards_trend"], priority=4 if important else 3)
