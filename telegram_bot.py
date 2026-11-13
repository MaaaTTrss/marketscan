"""Telegram-Befehle – werden in der Cloud bei jedem Lauf (~alle 15 Min.) abgearbeitet.

Nur Nachrichten aus DEINEM Chat (TELEGRAM_CHAT_ID) werden beachtet – fremde Leute,
die deinen Bot finden, können also nichts verändern.

Befehle:
  /kurs btc            Kurs & Signale
  /liste               alle Werte mit Tagesänderung
  /add dogecoin        Wert hinzufügen      /weg dogecoin   Wert entfernen
  /alarm btc < 70000   Preis-Alarm          /alarme  /alarmweg 2
  /kauf btc 0,05 70000 Kauf eintragen       /verkauf btc 0,02
  /depot               Depot-Übersicht      /bericht   Morgenbericht jetzt
"""
import re

import alerts
import private_store

HELP = """<b>📡 Markt-Radar – Befehle</b>

<b>Kurse</b>
/kurs btc – Kurs &amp; Signale eines Werts
/liste – alle Werte mit Tagesänderung

<b>Liste</b>
/add dogecoin – Wert hinzufügen
/weg dogecoin – Wert entfernen

<b>Preis-Alarme</b>
/alarm btc &lt; 70000 – meldet, wenn BTC unter 70.000 fällt
/alarm nvidia &gt; 200 – meldet, wenn Nvidia über 200 steigt
/alarme – alle Alarme · /alarmweg 2 – Alarm Nr. 2 löschen

<b>Depot</b>
/kauf btc 0,05 70000 – Kauf (Menge, Preis; ohne Preis = aktueller Kurs)
/verkauf btc 0,02 – Verkauf
/depot – Übersicht mit Gewinn/Verlust

/bericht – Morgenbericht sofort schicken

<i>Antworten kommen beim nächsten Update – das kann bis zu ~15 Minuten dauern.</i>"""

TREND = {"auf": "↗ Aufwärts", "ab": "↘ Abwärts", "gemischt": "→ gemischt"}


def num(s):
    """'0,05' / '70.000' / '70000' / '70k' -> float"""
    s = str(s).strip().lower().replace(" ", "").replace("$", "").replace("€", "")
    mult = 1000 if s.endswith("k") else 1
    s = s.rstrip("k")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return float(s) * mult


def fmt(v):
    return alerts.fmt_num(v) if v is not None else "–"


def find(query, items):
    """Wert in der Liste finden: Symbol (mit/ohne -USD) oder Name."""
    q = query.strip().lower()
    if not q:
        return None
    for a in items:
        if a["symbol"].lower() in (q, q + "-usd"):
            return a
    for a in items:
        if a["name"].lower() == q:
            return a
    hits = [a for a in items if a["name"].lower().startswith(q)] or [a for a in items if q in a["name"].lower()]
    return hits[0] if hits else None


def kurs_text(a, data):
    h = alerts._h
    lines = [f"<b>{h(a['name'])}</b> · {h(a['symbol'])}",
             f"<b>{fmt(a.get('price'))} $</b>  heute {alerts.pct(a['d1']) if a.get('d1') is not None else '–'}"
             f" · 7 T {alerts.pct(a['d7']) if a.get('d7') is not None else '–'}"]
    extra = []
    if a.get("rsi") is not None:
        extra.append(f"RSI {a['rsi']:.0f}")
    if a.get("trend"):
        extra.append(TREND.get(a["trend"], a["trend"]))
    if a.get("dd") is not None:
        extra.append(f"{abs(a['dd']) * 100:.0f} % unter Hoch")
    if extra:
        lines.append(" · ".join(extra))
    if a.get("earnings"):
        lines.append(f"📅 Quartalszahlen: {a['earnings'][8:10]}.{a['earnings'][5:7]}.")
    cur = [c for c in data["alerts"]["current"] if c.get("symbol") == a["symbol"]]
    for c in cur:
        lines.append(f"{alerts.ICON.get(c['kind'], '•')} {h(c['text'])}")
    return "\n".join(lines)


def liste_text(data):
    h = alerts._h
    out = ["<b>📋 Deine Werte</b>"]
    for g, title in (("crypto", "Krypto"), ("stock", "Aktien"), ("etf", "ETFs & Indizes")):
        part = [a for a in data["items"] if a.get("group") == g and a.get("price") is not None]
        if not part:
            continue
        rows = [f"{a['name'][:13]:<13} {fmt(a['price']):>10} {alerts.pct(a['d1']) if a.get('d1') is not None else '':>8}" for a in part]
        out.append(f"\n<b>{title}</b>\n<pre>" + h("\n".join(rows)) + "</pre>")
    return "\n".join(out)


def depot_text(data):
    h = alerts._h
    d = private_store.load()
    S = private_store.summary(d["depot"], data["items"])
    if not S["rows"]:
        return "💼 Dein Depot ist noch leer. Eintragen mit z. B. <code>/kauf btc 0,05 70000</code>"
    rows = [f"{r['name'][:11]:<11} {fmt(r['value']):>9} {alerts.pct(r['pl_pct']) if r.get('pl_pct') is not None else '':>8}"
            for r in S["rows"]]
    return (f"💼 <b>Depot</b>: {fmt(S['value'])} $\n"
            f"Heute {'+' if S['day'] >= 0 else '−'}{fmt(abs(S['day']))} $ ({alerts.pct(S['day_pct']) if S['day_pct'] is not None else '–'})\n"
            f"Gesamt {'+' if S['pl'] >= 0 else '−'}{fmt(abs(S['pl']))} $ ({alerts.pct(S['pl_pct']) if S['pl_pct'] is not None else '–'})\n"
            f"<pre>{h(chr(10).join(rows))}</pre>")


def alarme_text():
    al = private_store.load()["alarms"]
    if not al:
        return "🔔 Keine Preis-Alarme. Setzen mit z. B. <code>/alarm btc &lt; 70000</code>"
    return "🔔 <b>Preis-Alarme</b>\n" + "\n".join(
        f"{i}. {alerts._h(a['name'])} {'unter' if a['op'] == 'below' else 'über'} {fmt(a['price'])} $" for i, a in enumerate(al, 1))


def handle(text, data, client, changed):
    items = data["items"]
    parts = text.strip().split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0].lstrip("/")
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("start", "hilfe", "help", "?"):
        return HELP
    if cmd in ("kurs", "k", "preis"):
        a = find(arg, items)
        return kurs_text(a, data) if a else f"„{alerts._h(arg)}“ ist nicht in deiner Liste. Mit /add hinzufügen."
    if cmd == "liste":
        return liste_text(data)
    if cmd in ("add", "neu", "hinzufügen"):
        if not arg:
            return "Welchen Wert? z. B. <code>/add dogecoin</code>"
        if find(arg, items):
            return f"{alerts._h(find(arg, items)['name'])} ist schon in deiner Liste."
        res = client.get(f"/api/search?q={arg}").get_json() or []
        if not res:
            return f"Nichts gefunden zu „{alerts._h(arg)}“. Tipp: englischen Namen oder Kürzel probieren."
        x = res[0]
        r = client.post("/api/watchlist", json={"symbol": x["symbol"], "name": x["name"], "group": x["group"]})
        if r.status_code != 200:
            return f"Hat nicht geklappt: {alerts._h((r.get_json() or {}).get('error', ''))}"
        changed["watchlist"] = True
        return f"✅ <b>{alerts._h(x['name'])}</b> ({alerts._h(x['symbol'])}) hinzugefügt. Erscheint beim nächsten Update im Radar."
    if cmd in ("weg", "entfernen", "remove"):
        a = find(arg, items)
        if not a:
            return f"„{alerts._h(arg)}“ nicht gefunden."
        client.delete(f"/api/watchlist/{a['symbol']}")
        changed["watchlist"] = True
        return f"🗑 {alerts._h(a['name'])} entfernt."

    if cmd in ("alarm", "alarme", "alarmweg", "kauf", "verkauf", "depot") and not private_store.available():
        return "🔒 Depot und Alarme sind in der Cloud noch nicht eingerichtet (Schlüssel fehlt)."
    if cmd == "alarm":
        m = re.match(r"(.+?)\s*(<|>|unter|über|ueber)?\s*([\d.,]+k?)\s*$", arg, re.I)
        if not m:
            return "So geht's: <code>/alarm btc &lt; 70000</code> oder <code>/alarm nvidia &gt; 200</code>"
        a = find(m.group(1), items)
        if not a:
            return f"„{alerts._h(m.group(1))}“ ist nicht in deiner Liste."
        price = num(m.group(3))
        op = m.group(2)
        op = ("below" if op in ("<", "unter") else "above") if op else ("below" if price < (a.get("price") or 0) else "above")
        private_store.add_alarm(a["symbol"], a["name"], op, price)
        changed["private"] = True
        return (f"🔔 Alarm gesetzt: <b>{alerts._h(a['name'])}</b> {'unter' if op == 'below' else 'über'} {fmt(price)} $ "
                f"(jetzt {fmt(a.get('price'))} $)")
    if cmd == "alarme":
        return alarme_text()
    if cmd == "alarmweg":
        al = private_store.load()["alarms"]
        try:
            a = al[int(arg) - 1]
        except (ValueError, IndexError):
            return "Welche Nummer? Siehe /alarme – dann z. B. <code>/alarmweg 2</code>"
        private_store.remove_alarm(a["id"])
        changed["private"] = True
        return f"🗑 Alarm für {alerts._h(a['name'])} gelöscht."
    if cmd in ("kauf", "verkauf"):
        toks = arg.split()
        nums = []
        while toks and re.fullmatch(r"[\d.,]+k?\$?", toks[-1]):
            nums.insert(0, toks.pop())
        a = find(" ".join(toks), items)
        if not a or not nums:
            return f"So geht's: <code>/{cmd} btc 0,05 70000</code> (Menge, dann Preis pro Stück; Preis weglassen = aktueller Kurs)"
        qty = num(nums[0])
        price = num(nums[1]) if len(nums) > 1 else a.get("price")
        if not price:
            return "Kein Preis bekannt – bitte mit angeben."
        private_store.add_trade(a["symbol"], a["name"], qty if cmd == "kauf" else -qty, price)
        changed["private"] = True
        return (f"{'🟢 Kauf' if cmd == 'kauf' else '🔴 Verkauf'} eingetragen: {fmt(qty)} × <b>{alerts._h(a['name'])}</b> "
                f"zu {fmt(price)} $\n\n" + depot_text(data))
    if cmd == "depot":
        return depot_text(data)
    if cmd in ("bericht", "report"):
        changed["report"] = True
        return "☀️ Kommt sofort …"
    return "Unbekannter Befehl. /hilfe zeigt alle Befehle."


def process(data, client):
    """Neue Nachrichten holen, beantworten. Gibt zurück, was sich geändert hat."""
    changed = {"watchlist": False, "private": False, "report": False}
    token, chat = alerts.telegram_config()
    if not (token and chat):
        return changed
    try:
        updates = alerts.telegram_call("getUpdates", token=token, timeout=0, allowed_updates=["message"])
    except Exception as e:
        print("Telegram: Nachrichten abrufen fehlgeschlagen:", e)
        return changed
    last = None
    for u in updates:
        last = u["update_id"]
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != str(chat) or not msg.get("text", "").startswith("/"):
            continue   # nur deine eigenen Befehle
        try:
            reply = handle(msg["text"], data, client, changed)
        except Exception as e:
            reply = f"⚠️ Fehler: {alerts._h(str(e))}"
        print("Telegram-Befehl:", msg["text"].split()[0])
        alerts.send_telegram(reply, button=False)
    if last is not None:   # als gelesen markieren
        try:
            alerts.telegram_call("getUpdates", token=token, offset=last + 1, timeout=0)
        except Exception:
            pass
    return changed
