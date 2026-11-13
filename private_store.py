"""Private Daten (Depot & Preis-Alarme) – verschlüsselt gespeichert.

Das GitHub-Projekt ist öffentlich. Deshalb liegen Depot und Alarme nur als
verschlüsselte Datei  private.enc  im Projekt. Der Schlüssel steht
  - am Mac in .env als  PRIVATE_KEY
  - in der Cloud als GitHub-Secret  RADAR_PRIVATE_KEY
Ohne Schlüssel kann niemand lesen, was drinsteht.
"""
import json
import os
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from dotenv import dotenv_values, set_key

BASE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(BASE, "private.enc")
ENV_FILE = os.path.join(BASE, ".env")
EMPTY = {"alarms": [], "depot": []}


def _key(create=False):
    env = dotenv_values(ENV_FILE) if os.path.exists(ENV_FILE) else {}
    k = env.get("PRIVATE_KEY") or os.environ.get("RADAR_PRIVATE_KEY")
    if not k and create and not os.environ.get("GITHUB_ACTIONS"):
        k = Fernet.generate_key().decode()
        if not os.path.exists(ENV_FILE):
            open(ENV_FILE, "w").close()
        set_key(ENV_FILE, "PRIVATE_KEY", k, quote_mode="never")
    return k


def available():
    return bool(_key())


def load():
    k = _key()
    if not k or not os.path.exists(FILE):
        return json.loads(json.dumps(EMPTY))
    try:
        data = json.loads(Fernet(k.encode()).decrypt(open(FILE, "rb").read()))
    except (InvalidToken, ValueError):
        print("private.enc lässt sich nicht entschlüsseln (falscher Schlüssel?)")
        return json.loads(json.dumps(EMPTY))
    return {**EMPTY, **data}


def save(data):
    k = _key(create=True)
    if not k:
        raise RuntimeError("Kein Schlüssel für private Daten vorhanden.")
    with open(FILE, "wb") as f:
        f.write(Fernet(k.encode()).encrypt(json.dumps(data, ensure_ascii=False).encode()))


def new_id():
    return secrets.token_hex(3)


# ---------------------------------------------------------------- Alarme
def add_alarm(symbol, name, op, price=None, pct=None, ref=None):
    """Arten:
       below/above  – Preis unter/über 'price'
       drop/rise    – fällt/steigt um 'pct' ab dem aktuellen Kurs 'ref' (wird zu below/above umgerechnet)
       move         – Tagesbewegung von mehr als ±pct
       trail        – Nachzieh-Stopp: pct unter dem Hoch seit dem Setzen"""
    note = None
    if op in ("drop", "rise"):
        price = ref * (1 - pct) if op == "drop" else ref * (1 + pct)
        note = f"{'−' if op == 'drop' else '+'}{pct * 100:.0f} % ab {ref:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        op = "below" if op == "drop" else "above"
    d = load()
    a = {"id": new_id(), "symbol": symbol, "name": name, "op": op, "created": int(time.time()),
         "date": time.strftime("%Y-%m-%d")}
    if price is not None:
        a["price"] = float(price)
    if pct is not None and op in ("move", "trail"):
        a["pct"] = float(pct)
    if note:
        a["note"] = note
    d["alarms"].append(a)
    save(d)
    return a


def alarm_text(a):
    fmt = lambda v: f"{v:,.{0 if v >= 1000 else 2}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if a["op"] == "below":
        return f"fällt unter {fmt(a['price'])} $" + (f" ({a['note']})" if a.get("note") else "")
    if a["op"] == "above":
        return f"steigt über {fmt(a['price'])} $" + (f" ({a['note']})" if a.get("note") else "")
    if a["op"] == "move":
        return f"bewegt sich an einem Tag mehr als ±{a['pct'] * 100:.0f} %"
    if a["op"] == "trail":
        return f"Nachzieh-Stopp: {a['pct'] * 100:.0f} % unter dem Hoch" + (f" (Hoch {fmt(a['peak'])} $)" if a.get("peak") else "")
    return a["op"]


def remove_alarm(aid):
    d = load()
    d["alarms"] = [a for a in d["alarms"] if a["id"] != aid]
    save(d)


# ---------------------------------------------------------------- Depot
def add_trade(symbol, name, qty, price, date=None):
    """qty > 0 = Kauf, qty < 0 = Verkauf (zum Ø-Einstand, damit der Einstand stimmt)."""
    d = load()
    t = {"id": new_id(), "symbol": symbol, "name": name, "qty": float(qty), "price": float(price),
         "date": date or time.strftime("%Y-%m-%d")}
    d["depot"].append(t)
    save(d)
    return t


def remove_trade(tid):
    d = load()
    d["depot"] = [t for t in d["depot"] if t["id"] != tid]
    save(d)


def positions(depot):
    """Käufe/Verkäufe je Wert zusammenfassen: Menge, Einstand gesamt, Ø-Preis."""
    pos = {}
    for t in depot:
        p = pos.setdefault(t["symbol"], {"symbol": t["symbol"], "name": t["name"], "qty": 0.0, "cost": 0.0})
        if t["qty"] >= 0:
            p["qty"] += t["qty"]
            p["cost"] += t["qty"] * t["price"]
        else:   # Verkauf: Einstand anteilig abziehen
            avg = p["cost"] / p["qty"] if p["qty"] else 0
            sell = min(-t["qty"], p["qty"])
            p["qty"] -= sell
            p["cost"] -= sell * avg
    return [p for p in pos.values() if p["qty"] > 1e-12]


def lots(depot, symbol):
    """Einzelne Käufe, die noch im Depot sind (Verkäufe ziehen die ältesten zuerst ab – FIFO).
    Für die Krypto-Haltefrist: nach 1 Jahr sind Gewinne in Deutschland steuerfrei."""
    from datetime import date, timedelta
    open_lots = []
    for t in sorted([t for t in depot if t["symbol"] == symbol], key=lambda t: t["date"]):
        if t["qty"] >= 0:
            open_lots.append({"id": t["id"], "date": t["date"], "qty": t["qty"], "price": t["price"]})
        else:
            rest = -t["qty"]
            while rest > 1e-12 and open_lots:
                take = min(rest, open_lots[0]["qty"])
                open_lots[0]["qty"] -= take
                rest -= take
                if open_lots[0]["qty"] <= 1e-12:
                    open_lots.pop(0)
    today = date.today()
    for lot in open_lots:
        d = date.fromisoformat(lot["date"])
        try:
            free = d.replace(year=d.year + 1) + timedelta(days=1)
        except ValueError:   # 29. Februar
            free = d + timedelta(days=366)
        lot["taxfree"] = free.isoformat()
        lot["days_left"] = max(0, (free - today).days)
    return open_lots


def summary(depot, items):
    """Depot mit aktuellen Kursen bewerten. items = Werte aus dem Radar-Snapshot."""
    by = {a["symbol"]: a for a in items}
    rows, total, cost, day = [], 0.0, 0.0, 0.0
    week = 0.0
    for p in positions(depot):
        a = by.get(p["symbol"], {})
        price = a.get("price")
        val = p["qty"] * price if price is not None else None
        r = {**p, "avg": p["cost"] / p["qty"], "price": price, "value": val, "currency": a.get("currency", "USD"),
             "d1": a.get("d1"), "d7": a.get("d7"), "group": a.get("group")}
        if a.get("group") == "crypto":
            r["lots"] = lots(depot, p["symbol"])
        if val is not None:
            r["pl"] = val - p["cost"]
            r["pl_pct"] = val / p["cost"] - 1 if p["cost"] else None
            total += val
            cost += p["cost"]
            if a.get("d1") is not None:
                day += val - val / (1 + a["d1"])
            if a.get("d7") is not None:
                week += val - val / (1 + a["d7"])
        rows.append(r)
    for r in rows:
        r["weight"] = r["value"] / total if total and r.get("value") is not None else None
    rows.sort(key=lambda r: -(r.get("value") or 0))
    return {"rows": rows, "value": total, "cost": cost, "pl": total - cost,
            "pl_pct": total / cost - 1 if cost else None,
            "day": day, "day_pct": day / (total - day) if total - day else None,
            "week": week, "week_pct": week / (total - week) if total - week else None}
