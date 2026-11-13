"""Wie gut sind die Signale wirklich?

1) Rückblick: Die Regeln von MarketScan werden auf die letzten 3 Jahre jedes Werts
   angewendet. Für jedes Signal wird geschaut, was 7 und 30 Tage später passiert ist.
   Vergleich: "normal" = wie oft ein Wert an einem beliebigen Tag 30 Tage später höher stand.
2) Live: Jedes neue Signal ab jetzt wird gespeichert (signals_log.json) und später bewertet.
3) Spielgeld: Bei jedem neuen ★-Kaufsignal werden virtuell 1.000 $ gekauft und nach 30 Tagen
   verkauft – so sieht man live, ob es sich lohnt.
"""
import json
import os
from datetime import datetime, timedelta

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE, "signals_log.json")
YEARS = 3
PAPER_USD = 1000
PAPER_DAYS = 30

# Kategorie -> (Beschriftung, Richtung: +1 = sollte steigen, -1 = sollte fallen, Art)
CATS = {
    "good_star": ("★ Überverkauft im Aufwärtstrend", 1, "good"),
    "good": ("Überverkauft (Trend unklar)", 1, "good"),
    "low_down": ("Überverkauft im Abwärtstrend", 1, "info"),
    "bad_star": ("★ Überkauft im Abwärtstrend", -1, "bad"),
    "bad": ("Überkauft (Trend unklar)", -1, "bad"),
    "high_up": ("Überkauft im Aufwärtstrend", -1, "info"),
    "trend_up": ("Trendwechsel nach oben", 1, "good"),
    "trend_down": ("Trendwechsel nach unten", -1, "bad"),
}
GROUP_NAMES = {"crypto": "Krypto", "stock": "Aktien & ETFs"}


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def _trend(s):
    sma50, sma200 = s.rolling(50).mean(), s.rolling(200).mean()
    t = pd.Series("gemischt", index=s.index)
    t[(s > sma200) & (sma50 > sma200)] = "auf"
    t[(s < sma200) & (sma50 < sma200)] = "ab"
    t[sma200.isna()] = None
    return t


def fwd(s, date, days):
    """Rendite von 'date' bis 'date + days' (Kalendertage)."""
    try:
        p0 = s.asof(pd.Timestamp(date))
        end = pd.Timestamp(date) + timedelta(days=days)
        if end > s.index[-1]:
            return None
        p1 = s.asof(end)
        return float(p1 / p0 - 1) if p0 and p1 else None
    except Exception:
        return None


def events(s):
    """Alle Signal-Auslösungen in einer Kursreihe (wie die Live-Regeln)."""
    s = s.dropna()
    if len(s) < 260:
        return []
    r, t = _rsi(s), _trend(s)
    out = []
    prev_r, prev_t = None, None
    for d, rv, tv in zip(s.index, r.values, t.values):
        if tv is None or pd.isna(rv):
            prev_r, prev_t = rv, tv
            continue
        if prev_r is not None and not pd.isna(prev_r):
            if rv < 30 <= prev_r:
                out.append((d, "good_star" if tv == "auf" else "low_down" if tv == "ab" else "good"))
            elif rv > 70 >= prev_r:
                out.append((d, "bad_star" if tv == "ab" else "high_up" if tv == "auf" else "bad"))
        if prev_t is not None and tv != prev_t:
            if tv == "auf":
                out.append((d, "trend_up"))
            elif tv == "ab":
                out.append((d, "trend_down"))
        prev_r, prev_t = rv, tv
    return out


def _stats(rows, direction):
    out = {"n": len(rows)}
    for h in (7, 30):
        vals = [x[h] for x in rows if x.get(h) is not None]
        out[f"n{h}"] = len(vals)
        out[f"avg{h}"] = sum(vals) / len(vals) if vals else None
        out[f"hit{h}"] = (sum(1 for v in vals if v * direction > 0) / len(vals)) if vals else None
    return out


def backtest(hist, groups):
    """hist = {symbol: Kursreihe}, groups = {symbol: crypto/stock/etf}"""
    since = pd.Timestamp(datetime.now() - timedelta(days=365 * YEARS))
    by = {}          # (gruppe, kategorie) -> [ {7: r, 30: r} ]
    base = {}        # gruppe -> [r30 aller Tage]
    for sym, s in hist.items():
        g = "crypto" if groups.get(sym) == "crypto" else "stock"
        s = s.dropna()
        for d, cat in events(s):
            if d < since:
                continue
            by.setdefault((g, cat), []).append({7: fwd(s, d, 7), 30: fwd(s, d, 30), "sym": sym})
        days = s[s.index >= since]
        step = max(1, len(days) // 150)
        for d in days.index[::step]:
            v = fwd(s, d, 30)
            if v is not None:
                base.setdefault(g, []).append(v)
    result = {"years": YEARS, "groups": {}}
    for g in ("crypto", "stock"):
        b = base.get(g, [])
        rows = []
        for cat, (label, direction, kind) in CATS.items():
            st = _stats(by.get((g, cat), []), direction)
            if st["n"]:
                rows.append({"cat": cat, "label": label, "dir": direction, "kind": kind, **st})
        if rows or b:
            result["groups"][g] = {
                "name": GROUP_NAMES[g], "rows": rows,
                "base_up30": sum(1 for v in b if v > 0) / len(b) if b else None,
                "base_avg30": sum(b) / len(b) if b else None}
    # Spielgeld-Rückblick: jedes ★-Kaufsignal 1.000 $ für 30 Tage
    star = [x for g in ("crypto", "stock") for x in by.get((g, "good_star"), []) if x.get(30) is not None]
    result["paper_backtest"] = {"trades": len(star), "pl": sum(x[30] for x in star) * PAPER_USD,
                                "avg": sum(x[30] for x in star) / len(star) if star else None,
                                "wins": sum(1 for x in star if x[30] > 0)}
    return result


# ---------------------------------------------------------------- Live-Verlauf
def load_log():
    try:
        return json.load(open(LOG_FILE))
    except Exception:
        return []


def record(entries, items):
    """Neue Signale (aus alerts.check) mit Kurs speichern."""
    prices = {a["symbol"]: a.get("price") for a in items}
    log = load_log()
    today = datetime.now().strftime("%Y-%m-%d")
    for e in entries:
        if e.get("cat") and prices.get(e.get("symbol")):
            log.append({"date": today, "symbol": e["symbol"], "name": e["name"], "cat": e["cat"],
                        "price": prices[e["symbol"]]})
    with open(LOG_FILE, "w") as f:
        json.dump(log[-2000:], f)


def live(hist, items):
    """Live-Signale bewerten + Spielgeld-Depot."""
    now_price = {a["symbol"]: a.get("price") for a in items}
    log = load_log()
    rows = []
    for e in log:
        s = hist.get(e["symbol"])
        r = {**e}
        if s is not None:
            for h in (7, 30):
                r[f"r{h}"] = fwd(s, e["date"], h)
        cur = now_price.get(e["symbol"])
        r["now"] = cur / e["price"] - 1 if cur and e.get("price") else None
        rows.append(r)
    # Spielgeld: ★-Kaufsignale
    trades = []
    for r in rows:
        if r["cat"] != "good_star":
            continue
        age = (datetime.now() - datetime.strptime(r["date"], "%Y-%m-%d")).days
        closed = age >= PAPER_DAYS
        ret = r.get("r30") if closed else r.get("now")
        trades.append({"date": r["date"], "symbol": r["symbol"], "name": r["name"], "open": not closed,
                       "ret": ret, "pl": ret * PAPER_USD if ret is not None else None,
                       "until": (datetime.strptime(r["date"], "%Y-%m-%d") + timedelta(days=PAPER_DAYS)).strftime("%Y-%m-%d")})
    since = log[0]["date"] if log else datetime.now().strftime("%Y-%m-%d")
    stats = {}
    for cat, (label, direction, kind) in CATS.items():
        sel = [{7: r.get("r7"), 30: r.get("r30")} for r in rows if r["cat"] == cat]
        if sel:
            stats[cat] = {"label": label, "kind": kind, **_stats(sel, direction)}
    return {"since": since, "count": len(rows), "stats": stats, "recent": rows[-15:][::-1],
            "paper": {"usd": PAPER_USD, "days": PAPER_DAYS, "trades": trades[::-1],
                      "pl": sum(t["pl"] for t in trades if t["pl"] is not None),
                      "invested": PAPER_USD * len(trades)}}
