"""Markt-Radar als Live-App: läuft lokal auf deinem Mac, aktualisiert sich selbst,
und du kannst beliebige Coins, Aktien und ETFs suchen und hinzufügen.

Starten:  .venv/bin/python app.py      (oder Doppelklick auf "Radar starten.command")
Dann im Browser: http://127.0.0.1:8050
"""
import warnings

warnings.filterwarnings("ignore")
import json
import os
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd
import requests
import yfinance as yf
import secrets
import socket

from dotenv import dotenv_values, set_key
from flask import Flask, jsonify, make_response, request, send_from_directory

import alerts         # Auffälligkeiten & Mac-Benachrichtigungen
import private_store  # Depot & Preis-Alarme (verschlüsselt)
import radar          # Indikator-Funktionen (RSI, Fear & Greed, MVRV, ...)
import radar_config as cfg
import vergleich      # Strategie-Vergleich (Backtest)

BASE = os.path.dirname(os.path.abspath(__file__))
WL_FILE = os.path.join(BASE, "watchlist.json")
PORT = 8050

# Wie oft frische Daten geholt werden (Sekunden)
LIVE_TTL = 30            # aktuelle Kurse
HIST_TTL = 6 * 3600      # Tagesverlauf (für Trend, RSI, Hoch ...)
MARKET_TTL = 3600        # Fear & Greed, Shiller-KGV, Bitcoin-On-Chain
FUND_TTL = 24 * 3600     # Firmenkennzahlen

app = Flask(__name__, static_folder=os.path.join(BASE, "static"))
lock = threading.Lock()
cache = {"hist": {}, "live": (0.0, {}, ()), "fund": {}, "market": (0.0, None)}


# ---------------------------------------------------------------- Beobachtungsliste
def group_of(quote_type: str) -> str:
    return {"CRYPTOCURRENCY": "crypto", "EQUITY": "stock"}.get(quote_type, "etf")


def load_wl():
    if os.path.exists(WL_FILE):
        with open(WL_FILE, encoding="utf-8") as f:
            return json.load(f)
    wl = ([{"symbol": s, "name": n, "group": "crypto", "currency": "USD"} for s, n in cfg.CRYPTO.items()]
          + [{"symbol": s, "name": n, "group": "stock", "currency": "USD"} for s, n in cfg.STOCKS.items()]
          + [{"symbol": s, "name": n, "group": "etf", "currency": "USD"} for s, n in cfg.INDICES.items()])
    save_wl(wl)
    return wl


def save_wl(wl):
    with open(WL_FILE, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)
    push_files(["watchlist.json"], "Beobachtungsliste geändert")


git_lock = threading.Lock()


def _git(*a):
    import subprocess
    return subprocess.run(["git", *a], cwd=BASE, capture_output=True, text=True, timeout=60)


def git_ready():
    return os.path.isdir(os.path.join(BASE, ".git")) and bool(_git("remote").stdout.strip())


def push_files(files, msg):
    """Ist der Radar mit GitHub verbunden, Änderungen hochladen – dann hat auch die Cloud sie."""
    if os.environ.get("GITHUB_ACTIONS"):
        return   # in der Cloud lädt der Workflow selbst hoch
    threading.Thread(target=_push, args=(files, msg), daemon=True).start()


def git_pull():
    """Änderungen aus der Cloud holen (z. B. per Telegram hinzugefügte Werte)."""
    if os.environ.get("GITHUB_ACTIONS") or not git_ready():
        return
    with git_lock:
        _git("pull", "--rebase", "--autostash", "-q")


def _push(files, msg):
    if not git_ready():
        return
    with git_lock:
        try:
            _git("add", *files)
            if _git("diff", "--cached", "--quiet").returncode == 0:
                return
            _git("commit", "-m", msg)
            _git("pull", "--rebase", "--autostash")
            r = _git("push")
            print("Zu GitHub hochgeladen" if r.returncode == 0 else f"GitHub-Upload fehlgeschlagen: {r.stderr.strip()[:200]}")
        except Exception as e:
            print("GitHub-Upload fehlgeschlagen:", e)


# ---------------------------------------------------------------- Daten holen
def closes(df, symbols):
    df = df["Close"] if "Close" in df else df
    if isinstance(df, pd.Series):
        df = df.to_frame(symbols[0])
    return df


def get_hist(symbols, force=False):
    now = time.time()
    todo = [s for s in symbols
            if force or s not in cache["hist"] or now - cache["hist"][s][0] > HIST_TTL]
    if todo:
        df = closes(yf.download(todo, period="max", auto_adjust=True, progress=False), todo)
        for s in todo:
            if s in df and df[s].notna().any():
                cache["hist"][s] = (now, df[s].dropna())
    return {s: cache["hist"][s][1] for s in symbols if s in cache["hist"]}


def get_live(symbols):
    ts, prices, syms = cache["live"]
    if time.time() - ts < LIVE_TTL and set(symbols) <= set(syms):
        return prices
    prices = {}
    try:
        df = closes(yf.download(symbols, period="1d", interval="1m", progress=False), symbols)
        for s in symbols:
            if s in df and df[s].notna().any():
                ser = df[s].dropna()
                prices[s] = (float(ser.iloc[-1]), ser.index[-1])
    except Exception:
        pass
    cache["live"] = (time.time(), prices, tuple(symbols))
    return prices


def get_fund(symbols):
    now = time.time()
    todo = [s for s in symbols if s not in cache["fund"] or now - cache["fund"][s][0] > FUND_TTL]
    if todo:
        with ThreadPoolExecutor(8) as ex:
            for s, f in zip(todo, ex.map(radar.fundamentals, todo)):
                cache["fund"][s] = (now, f)
    return {s: cache["fund"][s][1] for s in symbols if s in cache["fund"]}


EARN_TTL = 24 * 3600
earn_cache = {}


def _earn_one(sym):
    try:
        cal = yf.Ticker(sym).calendar or {}
        ds = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ds:
            today = datetime.now().date()
            fut = sorted(d for d in ds if hasattr(d, "year") and d >= today)
            return fut[0].strftime("%Y-%m-%d") if fut else None
    except Exception:
        pass
    return None


def get_earnings(symbols):
    """Nächster Termin für Quartalszahlen je Aktie (einmal täglich abgefragt)."""
    now = time.time()
    todo = [s for s in symbols if s not in earn_cache or now - earn_cache[s][0] > EARN_TTL]
    if todo:
        with ThreadPoolExecutor(6) as ex:
            for s, d in zip(todo, ex.map(_earn_one, todo)):
                earn_cache[s] = (now, d)
    return {s: earn_cache[s][1] for s in symbols if s in earn_cache}


def clean(v):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return v
    return None if pd.isna(v) else v


def metrics(s: pd.Series, live):
    s = s.copy()
    if live:
        price, t = live
        day = pd.Timestamp(t).tz_convert("UTC").tz_localize(None).normalize()
        if day > s.index[-1]:
            s.loc[day] = price
        else:
            s.iloc[-1] = price
    price = float(s.iloc[-1])
    out = {"price": price,
           "d1": price / float(s.iloc[-2]) - 1 if len(s) > 1 else None,
           "d7": radar.change(s, 7), "d30": radar.change(s, 30),
           "ath": float(s.max()), "dd": price / float(s.max()) - 1,
           "rsi": radar.rsi(s) if len(s) > 15 else None,
           "trend": None, "vs200": None, "mom": None}
    if len(s) >= 200:
        sma50, sma200 = s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1]
        out["vs200"] = price / sma200 - 1
        out["trend"] = ("auf" if price > sma200 and sma50 > sma200 else
                        "ab" if price < sma200 and sma50 < sma200 else "gemischt")
    p30, p365 = radar.price_days_ago(s, 30), radar.price_days_ago(s, 365)
    if p30 and p365:
        out["mom"] = p30 / p365 - 1
    sp = s[s.index[-1] - pd.Timedelta(days=365):]
    step = max(1, len(sp) // 120)
    out["spark"] = [round(float(v), 6) for v in sp.iloc[::step]] + [price]
    return {k: (clean(v) if k != "spark" and k != "trend" else v) for k, v in out.items()}


def get_market():
    ts, data = cache["market"]
    if data and time.time() - ts < MARKET_TTL:
        return data
    with ThreadPoolExecutor(4) as ex:
        f1, f2, f3, f4 = (ex.submit(radar.crypto_fng), ex.submit(radar.stock_fng),
                          ex.submit(radar.shiller_cape), ex.submit(radar.btc_onchain))
        cfng, sfng, cape, onchain = f1.result(), f2.result(), f3.result(), f4.result()
    btc = None
    if onchain is not None and len(onchain) > 1000:
        price, mvrv = onchain["PriceUSD"], onchain["CapMVRVCur"]
        days_now, cycles = radar.halving_cycles(price)
        mv = mvrv[mvrv.index >= "2013-01-01"].iloc[::7]
        btc = {"mvrv": float(mvrv.iloc[-1]), "mvrv_pct": float((mvrv < mvrv.iloc[-1]).mean()),
               "mayer": float(price.iloc[-1] / price.rolling(200).mean().iloc[-1]),
               "days": days_now, "last_halving": cfg.HALVINGS[-1],
               "next_halving": cfg.NEXT_HALVING_ESTIMATE,
               "cycles": [{k: (str(v.date()) if k == "peak_date" else v) for k, v in c.items()}
                          for c in cycles],
               "mvrv_hist": [[d.strftime("%Y-%m-%d"), round(float(v), 3)] for d, v in mv.items()]}
    data = {"crypto_fng": cfng, "stock_fng": sfng, "cape": cape, "btc": btc}
    cache["market"] = (time.time(), data)
    return data


# ---------------------------------------------------------------- Handy-Zugriff (WLAN)
# Der Radar ist im WLAN erreichbar, damit du ihn auf dem Handy öffnen kannst.
# Am Mac selbst (127.0.0.1) geht alles ohne Schlüssel. Andere Geräte brauchen
# einen geheimen Schlüssel (steht in .env als RADAR_KEY), den sie einmal über
# den QR-Code bekommen – danach merkt sich das Handy ihn als Cookie.
ENV_FILE = os.path.join(BASE, ".env")
PUBLIC_PATHS = ("/manifest.webmanifest", "/static/icon-", "/favicon.ico")


def access_key(renew=False):
    env = dotenv_values(ENV_FILE) if os.path.exists(ENV_FILE) else {}
    k = env.get("RADAR_KEY")
    if renew or not k:
        k = secrets.token_urlsafe(18)
        if not os.path.exists(ENV_FILE):
            open(ENV_FILE, "a").close()
        set_key(ENV_FILE, "RADAR_KEY", k, quote_mode="never")
    return k


def is_local():
    return request.remote_addr in ("127.0.0.1", "::1")


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))   # schickt nichts, ermittelt nur die eigene WLAN-Adresse
        ip = s.getsockname()[0]
        s.close()
        return ip if not ip.startswith("127.") else None
    except OSError:
        return None


def phone_url():
    ip = lan_ip()
    return f"http://{ip}:{PORT}/?k={access_key()}" if ip else None


LOCKED_PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Markt-Radar</title><body style="font:16px -apple-system,sans-serif;padding:32px;max-width:420px;margin:auto">
<h2>🔒 Markt-Radar</h2><p>Bitte öffne den Radar über den QR-Code am Mac:<br>
<b>Start → Auffälligkeiten → „Auf dem Handy öffnen“</b>.</p></body>"""


@app.before_request
def check_access():
    if is_local() or request.path.startswith(PUBLIC_PATHS):
        return None
    key = access_key()
    given = request.args.get("k") or request.cookies.get("radar_key") or ""
    if secrets.compare_digest(given, key):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Kein Zugriff"}), 403
    return LOCKED_PAGE, 403


@app.after_request
def remember_key(resp):
    k = request.args.get("k")
    if k and not is_local() and secrets.compare_digest(k, access_key()):
        resp.set_cookie("radar_key", k, max_age=400 * 24 * 3600, samesite="Lax", httponly=True)
    return resp


@app.route("/api/phone")
def api_phone():
    if not is_local():
        return jsonify({"error": "Nur am Mac"}), 403
    return jsonify({"url": phone_url(), "ip": lan_ip()})


@app.route("/api/phone/qr.svg")
def api_phone_qr():
    import io
    import qrcode
    import qrcode.image.svg
    if not is_local() or not phone_url():
        return "", 404
    buf = io.BytesIO()
    qrcode.make(phone_url(), image_factory=qrcode.image.svg.SvgPathImage, border=2).save(buf)
    return buf.getvalue(), 200, {"Content-Type": "image/svg+xml"}


@app.route("/api/phone/renew", methods=["POST"])
def api_phone_renew():
    if not is_local():
        return jsonify({"error": "Nur am Mac"}), 403
    access_key(renew=True)
    return jsonify({"url": phone_url(), "ip": lan_ip()})


@app.route("/manifest.webmanifest")
def manifest():
    # start_url mit Schlüssel, damit das Icon auf dem Startbildschirm direkt reinkommt
    k = request.args.get("k", "")
    start = f"/?k={k}" if k and secrets.compare_digest(k, access_key()) else "/"
    data = {"name": "Markt-Radar", "short_name": "Radar", "start_url": start, "display": "standalone",
            "background_color": "#0f1115", "theme_color": "#0f1115",
            "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}]}
    resp = make_response(json.dumps(data))
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


# ---------------------------------------------------------------- API
@app.after_request
def no_cache(resp):
    # Browser soll nach Updates immer die neue Oberfläche laden
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def snapshot(force=False):
    with lock:
        if force:
            cache["live"] = (0.0, {}, ())
        wl = load_wl()
        syms = [w["symbol"] for w in wl]
        hist = get_hist(syms)
        live = get_live(syms) if syms else {}
        fund = get_fund([w["symbol"] for w in wl if w["group"] == "stock"])
        earn = get_earnings([w["symbol"] for w in wl if w["group"] == "stock"])
        market = get_market()
    items = []
    for w in wl:
        s = w["symbol"]
        item = dict(w)
        if s in hist and len(hist[s]) > 1:
            try:
                item.update(metrics(hist[s], live.get(s)))
            except Exception as e:
                item["error"] = str(e)
        else:
            item["error"] = "Keine Kursdaten"
        if w["group"] == "stock":
            item["fund"] = {k: clean(v) for k, v in fund.get(s, {}).items()}
            item["earnings"] = earn.get(s)
        items.append(item)
    snap = {"updated": datetime.now().strftime("%H:%M:%S"), "items": items, "market": market}
    with alert_lock:
        alerts.check(snap, private_store.load())
    return snap


@app.route("/api/data")
def api_data():
    snap = snapshot(force=request.args.get("force") == "1")
    snap["alerts"] = {"current": [c for c in alerts.conditions(snap, private_store.load()) if c["prio"] > 0],
                      "log": alerts.log()[:30], "settings": alerts.settings()}
    return jsonify(snap)


# ---------------------------------------------------------------- Benachrichtigungen
CHECK_EVERY = 5 * 60   # Sekunden zwischen zwei Prüfungen im Hintergrund
alert_lock = threading.Lock()


def background_checker():
    while True:
        try:
            git_pull()
            snapshot()
        except Exception as e:
            print("Hintergrund-Prüfung fehlgeschlagen:", e)
        time.sleep(CHECK_EVERY)


# ---------------------------------------------------------------- Depot & Preis-Alarme
def _item(sym):
    return next((w for w in load_wl() if w["symbol"] == sym), None)


@app.route("/api/private")
def api_private():
    d = private_store.load()
    snap = snapshot()
    return jsonify({"alarms": d["alarms"], "depot": d["depot"],
                    "summary": private_store.summary(d["depot"], snap["items"])})


@app.route("/api/alarms", methods=["POST"])
def api_alarm_add():
    j = request.get_json(force=True)
    it = _item(j.get("symbol"))
    if not it:
        return jsonify({"error": "Wert nicht in deiner Liste"}), 400
    try:
        price = float(str(j.get("price")).replace(",", "."))
    except ValueError:
        return jsonify({"error": "Preis ungültig"}), 400
    op = "above" if j.get("op") == "above" else "below"
    a = private_store.add_alarm(it["symbol"], it["name"], op, price)
    push_files(["private.enc"], "Preis-Alarm geändert")
    return jsonify(a)


@app.route("/api/alarms/<aid>", methods=["DELETE"])
def api_alarm_del(aid):
    private_store.remove_alarm(aid)
    push_files(["private.enc"], "Preis-Alarm geändert")
    return jsonify({"ok": True})


@app.route("/api/depot", methods=["POST"])
def api_depot_add():
    j = request.get_json(force=True)
    it = _item(j.get("symbol"))
    if not it:
        return jsonify({"error": "Wert nicht in deiner Liste"}), 400
    try:
        qty = float(str(j.get("qty")).replace(",", "."))
        price = float(str(j.get("price")).replace(",", "."))
    except ValueError:
        return jsonify({"error": "Menge oder Preis ungültig"}), 400
    if j.get("side") == "sell":
        qty = -abs(qty)
    t = private_store.add_trade(it["symbol"], it["name"], qty, price, j.get("date") or None)
    push_files(["private.enc"], "Depot geändert")
    return jsonify(t)


@app.route("/api/depot/<tid>", methods=["DELETE"])
def api_depot_del(tid):
    private_store.remove_trade(tid)
    push_files(["private.enc"], "Depot geändert")
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    s = alerts.settings()
    s.update({k: v for k, v in request.get_json(force=True).items() if k in ("mac_notifications", "telegram_notifications", "ntfy_notifications")})
    alerts.save_settings(s)
    return jsonify(s)


@app.route("/api/ntfy/status")
def api_ntfy_status():
    t = alerts.ntfy_topic()
    return jsonify({"topic": t, "url": f"{alerts.NTFY_SERVER}/{t}" if t else None})


@app.route("/api/ntfy/setup", methods=["POST"])
def api_ntfy_setup():
    t = alerts.ntfy_topic() or alerts.ntfy_new_topic()
    return jsonify({"topic": t, "url": f"{alerts.NTFY_SERVER}/{t}"})


@app.route("/api/ntfy/new", methods=["POST"])
def api_ntfy_new():
    t = alerts.ntfy_new_topic()
    return jsonify({"topic": t, "url": f"{alerts.NTFY_SERVER}/{t}"})


@app.route("/api/ntfy/qr.svg")
def api_ntfy_qr():
    import io
    import qrcode
    import qrcode.image.svg
    t = alerts.ntfy_topic()
    if not t:
        return "", 404
    buf = io.BytesIO()
    qrcode.make(f"{alerts.NTFY_SERVER}/{t}", image_factory=qrcode.image.svg.SvgPathImage, border=2).save(buf)
    return buf.getvalue(), 200, {"Content-Type": "image/svg+xml"}


@app.route("/api/ntfy/test", methods=["POST"])
def api_ntfy_test():
    ok = alerts.send_ntfy("Markt-Radar", "✅ Verbunden! Ab jetzt meldet sich der Radar hier, sobald etwas neu auffällig wird.",
                          tags=["satellite"])
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "Senden fehlgeschlagen – erst „Einrichten“ klicken."}), 400))


@app.route("/api/telegram/status")
def api_tg_status():
    token, chat = alerts.telegram_config()
    bot = None
    if token:
        try:
            bot = alerts.telegram_call("getMe", token=token)["username"]
        except Exception:
            bot = None
    return jsonify({"has_token": bool(token), "token_ok": bool(bot), "bot": bot, "has_chat": bool(chat)})


@app.route("/api/telegram/token", methods=["POST"])
def api_tg_token():
    try:
        bot = alerts.telegram_set_token(request.get_json(force=True).get("token", ""))
        return jsonify({"ok": True, "bot": bot})
    except Exception as e:
        return jsonify({"error": f"Token ungültig: {e}"}), 400


@app.route("/api/telegram/connect", methods=["POST"])
def api_tg_connect():
    try:
        name = alerts.telegram_find_chat()
        alerts.send_telegram(f"✅ Verbunden! Hallo {alerts._h(name)} – ab jetzt meldet sich der Markt-Radar hier, "
                             "sobald etwas neu auffällig wird.")
        return jsonify({"ok": True, "name": name})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/telegram/test", methods=["POST"])
def api_tg_test():
    ok = alerts.send_telegram(alerts.format_telegram(
        [{"kind": "info", "name": "Test", "text": "Handy-Benachrichtigungen funktionieren ✓"}]))
    return (jsonify({"ok": True}) if ok else (jsonify({"error": "Senden fehlgeschlagen"}), 400))


@app.route("/api/test-notification", methods=["POST"])
def api_test_notification():
    alerts.send_mac("Markt-Radar", "Test: Benachrichtigungen funktionieren ✓")
    return jsonify({"ok": True})


# ---------------------------------------------------------------- News
NEWS_TTL = 15 * 60
news_cache = {}
LEGAL = re.compile(r"\b(AG|SE|Inc\.?|Corp\.?|Corporation|Ltd\.?|plc|N\.V\.|S\.A\.|Holdings?|Class [A-C]|& Co\.? KGaA|KGaA|GmbH|Co\.)\s*$", re.I)


def news_query(item, lang):
    name = re.sub(r"\s+", " ", item["name"]).strip()
    for _ in range(3):
        name = LEGAL.sub("", name).strip(" ,.-")
    if item["group"] == "crypto":
        return f'"{name}" when:2d'
    if item["group"] == "stock":
        return f'"{name}" {"Aktie" if lang == "de" else "stock"} when:3d'
    return f'"{name}" when:7d'


def fetch_news(item, lang):
    key = (item["symbol"], lang)
    hit = news_cache.get(key)
    if hit and time.time() - hit[0] < NEWS_TTL:
        return hit[1]
    hl, gl = ("de", "DE") if lang == "de" else ("en-US", "US")
    r = requests.get("https://news.google.com/rss/search", timeout=10,
                     params={"q": news_query(item, lang), "hl": hl, "gl": gl,
                             "ceid": f"{gl}:{hl.split('-')[0]}"})
    out, seen = [], set()
    for it in ET.fromstring(r.content).findall("./channel/item"):
        src = it.findtext("source") or ""
        title = it.findtext("title") or ""
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        norm = re.sub(r"\W+", "", title.lower())[:60]
        if norm in seen:
            continue
        seen.add(norm)
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).timestamp()
        except Exception:
            ts = 0
        out.append({"title": title, "source": src, "url": it.findtext("link"), "ts": ts})
    out.sort(key=lambda x: -x["ts"])
    out = out[:25]
    news_cache[key] = (time.time(), out)
    return out


# ---------------------------------------------------------------- Detailseite eines Werts
profile_cache = {}
REC = {"strong_buy": "Stark kaufen", "buy": "Kaufen", "hold": "Halten",
       "underperform": "Untergewichten", "sell": "Verkaufen", "strong_sell": "Stark verkaufen"}


def get_profile(sym):
    hit = profile_cache.get(sym)
    if hit and time.time() - hit[0] < FUND_TTL:
        return hit[1]
    try:
        i = yf.Ticker(sym).info or {}
    except Exception:
        i = {}
    keys = ["sector", "industry", "country", "website", "fullTimeEmployees", "marketCap",
            "dividendYield", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "targetMeanPrice",
            "targetLowPrice", "targetHighPrice", "numberOfAnalystOpinions", "revenueGrowth",
            "earningsGrowth", "beta", "circulatingSupply", "maxSupply", "volume24Hr",
            "totalAssets", "category", "fundFamily", "netExpenseRatio", "exchange", "longName"]
    p = {k: clean(i.get(k)) if not isinstance(i.get(k), str) else i.get(k) for k in keys}
    p["summary"] = i.get("longBusinessSummary") or i.get("description") or ""
    p["recommendation"] = REC.get(i.get("recommendationKey"), None)
    profile_cache[sym] = (time.time(), p)
    return p


@app.route("/api/asset")
def api_asset():
    sym = request.args.get("symbol", "")
    item = next((w for w in load_wl() if w["symbol"] == sym), None)
    if not item:
        return jsonify({"error": "Dieser Wert ist nicht in deiner Liste."}), 404
    with lock:
        hist = get_hist([sym]).get(sym)
        live = get_live([w["symbol"] for w in load_wl()]).get(sym)
    if hist is None or len(hist) < 2:
        return jsonify({"error": "Keine Kursdaten"}), 400
    s = hist.copy()
    if live:
        day = pd.Timestamp(live[1]).tz_convert("UTC").tz_localize(None).normalize()
        if day > s.index[-1]:
            s.loc[day] = live[0]
        else:
            s.iloc[-1] = live[0]
    sma50, sma200 = s.rolling(50).mean(), s.rolling(200).mean()
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn)
    r = lambda ser, n=6: [None if pd.isna(v) else round(float(v), n) for v in ser]
    out = dict(item)
    out.update(metrics(hist, live))
    out.pop("spark", None)
    out["series"] = {"dates": [x.strftime("%Y-%m-%d") for x in s.index], "close": r(s),
                     "sma50": r(sma50), "sma200": r(sma200), "rsi": r(rsi, 1)}
    out["profile"] = get_profile(sym)
    if item["group"] == "stock":
        out["fund"] = {k: clean(v) for k, v in get_fund([sym]).get(sym, {}).items()}
    return jsonify(out)


@app.route("/api/backtest")
def api_backtest():
    sym = request.args.get("symbol", "")
    item = next((w for w in load_wl() if w["symbol"] == sym), None)
    if not item:
        return jsonify({"error": "Unbekannter Wert"}), 404
    hit = bt_cache.get(sym)
    if hit and time.time() - hit[0] < HIST_TTL:
        return jsonify(hit[1])
    try:
        res = vergleich.run(sym, item["group"])
    except Exception as e:
        return jsonify({"error": f"Test nicht möglich: {e}"}), 400
    res["name"], res["currency"] = item["name"], item.get("currency", "USD")
    bt_cache[sym] = (time.time(), res)
    return jsonify(res)


bt_cache = {}


@app.route("/api/news")
def api_news():
    sym, lang = request.args.get("symbol", ""), request.args.get("lang", "de")
    item = next((w for w in load_wl() if w["symbol"] == sym), None)
    if not item:
        return jsonify({"error": "Unbekannter Wert"}), 404
    try:
        return jsonify({"query": news_query(item, lang), "items": fetch_news(item, lang)})
    except Exception as e:
        return jsonify({"error": f"News gerade nicht abrufbar ({e.__class__.__name__})"}), 502


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if len(q) < 1:
        return jsonify([])
    try:
        quotes = yf.Search(q, max_results=12, news_count=0).quotes
    except Exception:
        return jsonify([])
    allowed = {"CRYPTOCURRENCY", "EQUITY", "ETF", "INDEX", "MUTUALFUND"}
    res = [{"symbol": x["symbol"],
            "name": (x.get("longname") or x.get("shortname") or x["symbol"]).strip(),
            "type": x.get("quoteType"), "group": group_of(x.get("quoteType")),
            "exchange": x.get("exchDisp", "")}
           for x in quotes if x.get("quoteType") in allowed and x.get("symbol")]
    return jsonify(res)


@app.route("/api/watchlist", methods=["POST"])
def api_add():
    body = request.get_json(force=True)
    sym = str(body.get("symbol", "")).strip().upper()
    if not sym:
        return jsonify({"error": "Kein Symbol"}), 400
    with lock:
        wl = load_wl()
        if any(w["symbol"] == sym for w in wl):
            return jsonify({"ok": True})
        h = get_hist([sym], force=True)
        if sym not in h or len(h[sym]) < 2:
            return jsonify({"error": f"Für {sym} gibt es keine Kursdaten."}), 400
        try:
            currency = yf.Ticker(sym).fast_info.currency or "USD"
        except Exception:
            currency = "USD"
        group = body.get("group") or ("crypto" if sym.endswith("-USD") else "stock")
        wl.append({"symbol": sym, "name": body.get("name") or sym,
                   "group": group, "currency": currency.upper()})
        save_wl(wl)
        cache["live"] = (0.0, {}, ())
    return jsonify({"ok": True})


@app.route("/api/watchlist/<path:sym>", methods=["DELETE"])
def api_remove(sym):
    with lock:
        wl = [w for w in load_wl() if w["symbol"] != sym]
        save_wl(wl)
    return jsonify({"ok": True})


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"Markt-Radar läuft auf {url}  (Beenden: Strg+C)")
    if lan_ip():
        print(f"Auf dem Handy (gleiches WLAN): QR-Code im Radar unter „Auf dem Handy öffnen“")
    threading.Thread(target=background_checker, daemon=True).start()
    if os.environ.get("RADAR_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
