"""Markt-Radar: sammelt Kennzahlen zu deiner Beobachtungsliste und baut daraus
eine Übersichtsseite (radar.html), die sich im Browser öffnet.

Aufruf:  .venv/bin/python radar.py            (öffnet die Seite danach)
         .venv/bin/python radar.py --no-open

Farben überall gleich: GRÜN = dieser Indikator spricht historisch eher für einen
Kauf, ROT = eher Vorsicht, GELB = neutral/gemischt. Das ist keine Kaufempfehlung,
sondern eine Zusammenfassung von Kennzahlen.
"""
import warnings

warnings.filterwarnings("ignore")
import argparse
import html
import re
import subprocess
from datetime import datetime

import pandas as pd
import requests
import yfinance as yf

import radar_config as cfg

UA = {"User-Agent": "Mozilla/5.0 (Macintosh) markt-radar"}


# ---------------------------------------------------------------- Kennzahlen
def rsi(s: pd.Series, n: int = 14) -> float:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return float((100 - 100 / (1 + up / dn)).iloc[-1])


def price_days_ago(s: pd.Series, days: int):
    past = s[: s.index[-1] - pd.Timedelta(days=days)]
    return float(past.iloc[-1]) if len(past) else None


def change(s, days):
    p = price_days_ago(s, days)
    return s.iloc[-1] / p - 1 if p else None


def analyze(s: pd.Series) -> dict:
    s = s.dropna()
    price = float(s.iloc[-1])
    sma50, sma200 = s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1]
    if price > sma200 and sma50 > sma200:
        trend = "auf"
    elif price < sma200 and sma50 < sma200:
        trend = "ab"
    else:
        trend = "gemischt"
    p30, p365 = price_days_ago(s, 30), price_days_ago(s, 365)
    return {
        "price": price,
        "d1": change(s, 1), "d7": change(s, 7), "d30": change(s, 30),
        "ath": float(s.max()), "dd": price / float(s.max()) - 1,
        "trend": trend, "vs200": price / sma200 - 1,
        "rsi": rsi(s),
        "mom": (p30 / p365 - 1) if p30 and p365 else None,
        "spark": s[s.index[-1] - pd.Timedelta(days=365):],
    }


def load_prices(tickers):
    df = yf.download(list(tickers), period="max", auto_adjust=True, progress=False)["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(list(tickers)[0])
    return {t: df[t].dropna() for t in tickers if t in df and df[t].notna().sum() > 200}


def fundamentals(ticker):
    try:
        i = yf.Ticker(ticker).info
    except Exception:
        return {}
    de = i.get("debtToEquity")
    return {"pe": i.get("trailingPE"), "fpe": i.get("forwardPE"),
            "pb": i.get("priceToBook"), "roe": i.get("returnOnEquity"),
            "margin": i.get("profitMargins"), "de": de / 100 if de else None}


def crypto_fng():
    try:
        data = requests.get("https://api.alternative.me/fng/?limit=365", timeout=15).json()["data"]
        vals = [int(d["value"]) for d in data]  # neuestes zuerst
        return {"now": vals[0], "label": data[0]["value_classification"],
                "d1": vals[1], "w1": vals[7], "m1": vals[30], "hist": vals[::-1]}
    except Exception:
        return None


def stock_fng():
    try:
        d = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                         headers=UA, timeout=15).json()["fear_and_greed"]
        return {"now": round(d["score"]), "label": d["rating"],
                "d1": round(d["previous_close"]), "w1": round(d["previous_1_week"]),
                "m1": round(d["previous_1_month"]), "y1": round(d["previous_1_year"])}
    except Exception:
        return None


def shiller_cape():
    try:
        txt = requests.get("https://www.multpl.com/shiller-pe/table/by-month",
                           headers=UA, timeout=15).text
        vals = [float(v) for v in re.findall(
            r"<td>[A-Z][a-z]{2} \d+, \d{4}</td>\s*<td>\s*(?:&#x2002;)?\s*([\d.]+)", txt)]
        cur, hist = vals[0], pd.Series(vals[1:])
        return {"now": cur, "median": float(hist.median()),
                "pct": float((hist < cur).mean())}
    except Exception:
        return None


def btc_onchain():
    """BTC-Kurs + MVRV seit 2010 von CoinMetrics (kostenlose Community-API)."""
    try:
        url = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
               "?assets=btc&metrics=PriceUSD,CapMVRVCur&frequency=1d"
               "&start_time=2010-07-18&page_size=10000&paging_from=start")
        rows = requests.get(url, timeout=30).json()["data"]
        df = pd.DataFrame(rows)
        df.index = pd.to_datetime(df["time"]).dt.tz_localize(None).dt.normalize()
        return df[["PriceUSD", "CapMVRVCur"]].astype(float).dropna()
    except Exception:
        return None


def halving_cycles(price: pd.Series):
    today = price.index[-1]
    last = pd.Timestamp(cfg.HALVINGS[-1])
    days_now = (today - last).days
    cycles = []
    for h in cfg.HALVINGS:
        h = pd.Timestamp(h)
        seg = price[h:h + pd.Timedelta(days=1100)]
        start = float(seg.iloc[0])
        at_same = seg[: h + pd.Timedelta(days=days_now)]
        peak_date = seg[: h + pd.Timedelta(days=900)].idxmax()
        peak = float(price[peak_date])
        after_peak = price[peak_date: peak_date + pd.Timedelta(days=500)]
        current = h == last
        cycles.append({
            "year": h.year, "start": start,
            "same": float(at_same.iloc[-1]) / start - 1,
            "peak_days": (peak_date - h).days, "peak_x": peak / start,
            "bottom": float(after_peak.min()) / peak - 1,
            "current": current,
            "peak_date": peak_date,
        })
    return days_now, cycles


# ---------------------------------------------------------------- HTML-Bausteine
def esc(x):
    return html.escape(str(x))


def pct(v, digits=1, sign=True):
    if v is None or pd.isna(v):
        return "–"
    return f"{v * 100:+.{digits}f} %" if sign else f"{v * 100:.{digits}f} %"


def money(v):
    if v >= 1000:
        return f"{v:,.0f} $".replace(",", ".")
    if v >= 1:
        return f"{v:,.2f} $".replace(".", ",")
    return f"{v:.4f} $".replace(".", ",")


def num(v, d=1):
    return "–" if v is None or pd.isna(v) else f"{v:.{d}f}".replace(".", ",")


def chg_cell(v):
    cls = "" if v is None else ("up" if v > 0 else "down")
    return f'<td class="num {cls}" data-v="{v if v is not None else -999}">{pct(v)}</td>'


def spark(s: pd.Series, w=120, h=32):
    s = s.dropna()
    if len(s) < 2:
        return ""
    step = max(1, len(s) // 120)
    s = s.iloc[::step]
    lo, hi = s.min(), s.max()
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(s) - 1):.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}"
                   for i, v in enumerate(s))
    color = "var(--good)" if s.iloc[-1] >= s.iloc[0] else "var(--bad)"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


def trend_cell(a):
    label = {"auf": "Aufwärts", "ab": "Abwärts", "gemischt": "Gemischt"}[a["trend"]]
    cls = {"auf": "good", "ab": "bad", "gemischt": "mid"}[a["trend"]]
    return (f'<td data-v="{a["vs200"]:.4f}"><span class="pill {cls}">{label}</span>'
            f'<div class="sub">{pct(a["vs200"], 0)} ggü. 200T</div></td>')


def rsi_cell(v):
    if v < 30:
        cls, label = "good", "überverkauft"
    elif v > 70:
        cls, label = "bad", "überkauft"
    else:
        cls, label = "mid", "neutral"
    return (f'<td data-v="{v:.1f}"><span class="pill {cls}">{v:.0f}</span>'
            f'<div class="sub">{label}</div></td>')


def dd_cell(v):
    width = min(100, abs(v) * 100)
    return (f'<td data-v="{v:.4f}"><div class="num">{pct(v, 0)}</div>'
            f'<div class="bar"><span style="width:{width:.0f}%"></span></div></td>')


def reversal_cell(v, threshold):
    if v is None:
        return '<td class="z">–</td>'
    note = '<div class="sub">stark gefallen</div>' if v < -threshold else ""
    cls = "up" if v > 0 else "down"
    return f'<td class="z num {cls}" data-v="{v:.4f}">{pct(v)}{note}</td>'


def graded(v, good, bad, fmt, lower_is_better=True):
    if v is None or pd.isna(v):
        return '<td class="num">–</td>'
    if lower_is_better:
        cls = "good" if v <= good else "bad" if v >= bad else "mid"
    else:
        cls = "good" if v >= good else "bad" if v <= bad else "mid"
    if v < 0 and lower_is_better:  # negatives KGV = Verlust
        cls = "bad"
    return f'<td class="num" data-v="{v:.4f}"><span class="pill {cls}">{fmt(v)}</span></td>'


def fng_card(title, d, note, extra_rows=""):
    if not d:
        return f'<div class="card"><h3>{title}</h3><p class="muted">Gerade nicht abrufbar.</p></div>'
    v = d["now"]
    if v <= 25:
        cls = "good"
    elif v >= 75:
        cls = "bad"
    else:
        cls = "mid"
    hist = ""
    if d.get("hist"):
        hist = spark(pd.Series(d["hist"]), w=260, h=40).replace(
            'class="spark"', 'class="spark wide"')
    return f"""<div class="card">
  <h3>{title}</h3>
  <div class="big"><span class="pill {cls} lg">{v}</span> <span class="lbl">{esc(d['label']).title()}</span></div>
  <div class="gauge"><span style="left:{v}%"></span></div>
  <div class="gscale"><span>Extreme Angst</span><span>Extreme Gier</span></div>
  <div class="mini"><span>Gestern <b>{d['d1']}</b></span><span>Vor 1 Woche <b>{d['w1']}</b></span><span>Vor 1 Monat <b>{d['m1']}</b></span>{extra_rows}</div>
  {hist}
  <p class="note">{note}</p>
</div>"""


# ---------------------------------------------------------------- Seite bauen
def build(crypto, stocks, fund, cfng, sfng, cape, onchain):
    now = datetime.now().strftime("%d.%m.%Y, %H:%M Uhr")

    # Krypto-Tabelle
    crow = ""
    for t, name in cfg.CRYPTO.items():
        if t not in crypto:
            continue
        a = crypto[t]
        crow += f"""<tr><td><b>{esc(name)}</b><div class="sub">{esc(t.replace('-USD', ''))}</div></td>
<td class="num" data-v="{a['price']}">{money(a['price'])}</td>{chg_cell(a['d1'])}{chg_cell(a['d7'])}{chg_cell(a['d30'])}
<td>{spark(a['spark'])}</td>{dd_cell(a['dd'])}{trend_cell(a)}{rsi_cell(a['rsi'])}
<td class="z num" data-v="{a['mom'] if a['mom'] is not None else -999}">{pct(a['mom'], 0)}</td>
{reversal_cell(a['d7'], 0.10)}</tr>"""

    # Aktien-Tabelle
    srow = ""
    for t, name in {**cfg.STOCKS, **cfg.INDICES}.items():
        if t not in stocks:
            continue
        a, f = stocks[t], fund.get(t, {})
        is_index = t in cfg.INDICES
        if is_index:
            fcells = '<td class="num muted" data-v="-999">Index</td>' + '<td class="num muted" data-v="-999">–</td>' * 5
        else:
            fcells = (graded(f.get("pe"), 15, 35, lambda v: num(v, 1))
                      + graded(f.get("fpe"), 15, 30, lambda v: num(v, 1))
                      + graded(f.get("pb"), 3, 15, lambda v: num(v, 1))
                      + graded(f.get("roe"), 0.15, 0.05, lambda v: pct(v, 0, False), False)
                      + graded(f.get("margin"), 0.15, 0.05, lambda v: pct(v, 0, False), False)
                      + graded(f.get("de"), 0.5, 2.0, lambda v: num(v, 2)))
        srow += f"""<tr{' class="idx"' if is_index else ''}><td><b>{esc(name)}</b><div class="sub">{esc(t)}</div></td>
<td class="num" data-v="{a['price']}">{money(a['price'])}</td>{chg_cell(a['d1'])}{chg_cell(a['d30'])}
<td>{spark(a['spark'])}</td>{dd_cell(a['dd'])}{trend_cell(a)}{rsi_cell(a['rsi'])}{fcells}
<td class="z num" data-v="{a['mom'] if a['mom'] is not None else -999}">{pct(a['mom'], 0)}</td>
{reversal_cell(a['d7'], 0.05)}</tr>"""

    # Bitcoin-Zyklus
    cycle_html = '<p class="muted">On-Chain-Daten gerade nicht abrufbar.</p>'
    if onchain is not None and len(onchain) > 1000:
        price = onchain["PriceUSD"]
        mvrv = onchain["CapMVRVCur"]
        days_now, cycles = halving_cycles(price)
        m = float(mvrv.iloc[-1])
        m_cls = "good" if m < 1 else "bad" if m > 3 else "mid"
        m_txt = ("historisch selten so niedrig – Markt unter Einstandspreis" if m < 1 else
                 "historisch überhitzt" if m > 3 else
                 "erhöht" if m > 2.4 else "neutraler Bereich")
        mayer = float(price.iloc[-1] / price.rolling(200).mean().iloc[-1])
        y_cls = "good" if mayer < 0.8 else "bad" if mayer > 2.4 else "mid"
        mvrv_pct = float((mvrv < m).mean())
        rows = ""
        for c in cycles:
            cur = c["current"]
            peak_txt = (f"bisher {c['peak_days']} Tage, ×{c['peak_x']:.1f}" if cur
                        else f"nach {c['peak_days']} Tagen, ×{c['peak_x']:.1f}")
            bottom_txt = ("bisher " if cur else "") + pct(c["bottom"], 0)
            rows += (f'<tr{" class=cur" if cur else ""}><td>{c["year"]}{" (jetzt)" if cur else ""}</td>'
                     f'<td class="num">{money(c["start"])}</td>'
                     f'<td class="num {"up" if c["same"] > 0 else "down"}">{pct(c["same"], 0)}</td>'
                     f'<td>{peak_txt}</td><td class="num down">{bottom_txt}</td></tr>')
        mv = mvrv[mvrv.index >= "2013-01-01"].iloc[::7]
        cycle_html = f"""
<div class="grid3">
  <div class="card"><h3>MVRV <span class="ev ev-m">Belege: mittel</span></h3>
    <div class="big"><span class="pill {m_cls} lg">{m:.2f}</span> <span class="lbl">{m_txt}</span></div>
    <p class="note">Marktwert ÷ durchschnittlicher Einkaufspreis aller Coins. Unter 1 = Durchschnitt im Minus
    (war oft nahe Tiefpunkten), über 3 = viele sitzen auf großen Gewinnen (war oft nahe Hochs).
    Heute höher als an {mvrv_pct:.0%} aller Tage seit 2010.</p>
    {mvrv_chart(mv)}
  </div>
  <div class="card"><h3>Mayer-Multiple</h3>
    <div class="big"><span class="pill {y_cls} lg">{mayer:.2f}</span> <span class="lbl">Kurs ÷ 200-Tage-Schnitt</span></div>
    <p class="note">Unter 0,8 war Bitcoin historisch günstig, über 2,4 überhitzt.</p>
  </div>
  <div class="card"><h3>Halving-Uhr</h3>
    <div class="big"><span class="pill mid lg">{days_now}</span> <span class="lbl">Tage seit dem Halving {cfg.HALVINGS[-1][:4]}</span></div>
    <p class="note">Nächstes Halving: {cfg.NEXT_HALVING_ESTIMATE}. In früheren Zyklen lag das Hoch 12–18 Monate
    nach dem Halving, danach folgten −75 % bis −85 %.</p>
  </div>
</div>
<div class="card"><h3>Frühere Zyklen im Vergleich <span class="ev ev-w">Belege: schwach – nur 4 Zyklen</span></h3>
<div class="scroll"><table class="data">
<thead><tr><th>Halving</th><th>Kurs am Halving</th><th>Veränderung nach {days_now} Tagen</th><th>Zyklus-Hoch</th><th>Absturz danach</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="note">Vier Datenpunkte sind statistisch sehr wenig. Ob sich das Muster wiederholt, weiß niemand.</p></div>"""

    cape_html = '<p class="muted">Gerade nicht abrufbar.</p>'
    if cape:
        c_cls = "bad" if cape["pct"] > 0.85 else "good" if cape["pct"] < 0.3 else "mid"
        cape_html = f"""<div class="big"><span class="pill {c_cls} lg">{num(cape['now'], 1)}</span>
<span class="lbl">höher als {cape['pct']:.0%} aller Monate seit 1871</span></div>
<p class="note">Langjähriger Median: {num(cape['median'], 1)}. Ein hoher Wert sagt eher niedrigere Renditen
für die <b>nächsten 10 Jahre</b> voraus – für kurzfristiges Timing taugt er nicht.</p>"""

    sfng_extra = f"<span>Vor 1 Jahr <b>{sfng['y1']}</b></span>" if sfng else ""

    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Markt-Radar</title><style>{CSS}</style></head><body>
<header><div><h1>Markt-Radar</h1><p class="muted">Stand: {now}</p></div>
<div class="legend"><span class="pill good">spricht eher für Kauf</span><span class="pill mid">neutral</span><span class="pill bad">eher Vorsicht</span></div></header>

<section><h2>Marktstimmung</h2><div class="grid3">
{fng_card('Krypto Fear &amp; Greed <span class="ev ev-w">Belege: schwach–mittel</span>', cfng,
          'Extreme Angst (unter 25) lag historisch öfter in der Nähe von Tiefpunkten, extreme Gier (über 75) öfter in der Nähe von Hochs. Verlauf: letzte 12 Monate.')}
{fng_card('Aktien Fear &amp; Greed (CNN) <span class="ev ev-w">Belege: schwach</span>', sfng,
          'Misst die Stimmung am US-Aktienmarkt aus 7 Teilwerten (Volatilität, Momentum, Optionen u. a.).', sfng_extra)}
<div class="card z"><h3>Shiller-KGV (S&amp;P 500) <span class="tag">Zusatzinfo</span></h3>{cape_html}</div>
</div></section>

<section><h2>Krypto</h2><div class="card"><div class="scroll"><table class="data sortable">
<thead><tr><th>Coin</th><th data-col="1">Kurs</th><th data-col="2">1 Tag</th><th data-col="3">7 Tage</th><th data-col="4">30 Tage</th><th>1 Jahr</th>
<th data-col="6">vom Hoch</th><th data-col="7">Trend <span class="ev ev-s">stark</span></th><th data-col="8">RSI <span class="ev ev-w">schwach</span></th>
<th data-col="9" class="z">Momentum<br><span class="tag">Zusatz</span></th><th data-col="10" class="z">Umkehr 1W<br><span class="tag">Zusatz</span></th></tr></thead>
<tbody>{crow}</tbody></table></div></div></section>

<section><h2>Bitcoin-Zyklus</h2>{cycle_html}</section>

<section><h2>Aktien &amp; Indizes</h2><div class="card"><div class="scroll"><table class="data sortable">
<thead><tr><th rowspan="2">Wert</th><th rowspan="2" data-col="1">Kurs</th><th rowspan="2" data-col="2">1 Tag</th><th rowspan="2" data-col="3">30 Tage</th><th rowspan="2">1 Jahr</th>
<th rowspan="2" data-col="5">vom Hoch</th><th rowspan="2" data-col="6">Trend <span class="ev ev-s">stark</span></th><th rowspan="2" data-col="7">RSI <span class="ev ev-w">schwach</span></th>
<th colspan="3" class="grp">Value – wie günstig? <span class="ev ev-m">mittel</span></th>
<th colspan="3" class="grp">Qualität – wie solide? <span class="ev ev-m">mittel</span></th>
<th rowspan="2" data-col="14" class="z">Momentum<br><span class="tag">Zusatz</span></th><th rowspan="2" data-col="15" class="z">Umkehr 1W<br><span class="tag">Zusatz</span></th></tr>
<tr><th data-col="8">KGV</th><th data-col="9">KGV erw.</th><th data-col="10">KBV</th><th data-col="11">EK-Rendite</th><th data-col="12">Marge</th><th data-col="13">Schulden/EK</th></tr></thead>
<tbody>{srow}</tbody></table></div></div></section>

<section><h2>Was bedeuten die Werte?</h2><div class="card explain">
<dl>
<dt>vom Hoch</dt><dd>Wie weit der Kurs unter seinem bisherigen Allzeithoch liegt.</dd>
<dt>Trend</dt><dd>Aufwärts = Kurs über dem 200-Tage-Schnitt und 50-Tage- über 200-Tage-Schnitt. Am besten belegter Filter, schützt vor allem vor großen Abstürzen.</dd>
<dt>RSI (14 Tage)</dt><dd>Unter 30 = in kurzer Zeit stark gefallen („überverkauft“), über 70 = stark gestiegen. Bei Krypto bleiben starke Werte oft lange „überkauft“.</dd>
<dt>KGV / KGV erw.</dt><dd>Kurs ÷ Gewinn der letzten 12 Monate bzw. erwarteter Gewinn. Niedriger = günstiger. Wachstumsfirmen sind fast immer „teuer“.</dd>
<dt>KBV</dt><dd>Kurs ÷ Buchwert (Eigenkapital). Bei Tech-Firmen meist hoch, weil ihr Wert in Software/Marken steckt.</dd>
<dt>EK-Rendite, Marge, Schulden/EK</dt><dd>Wie profitabel und wie solide finanziert die Firma ist. Hohe Rendite + hohe Marge + wenig Schulden = hohe Qualität.</dd>
<dt>Momentum (Zusatz)</dt><dd>Kursveränderung von vor 12 Monaten bis vor 1 Monat. Werte mit starkem Momentum steigen statistisch oft weiter.</dd>
<dt>Umkehr 1W (Zusatz)</dt><dd>Veränderung der letzten 7 Tage. Sehr starke Wochenverlierer erholen sich manchmal kurzfristig – nach Gebühren meist zu klein, um davon zu leben.</dd>
</dl>
<p class="note">Spaltenköpfe in den Tabellen anklicken zum Sortieren. Keine Anlageberatung – Kennzahlen zeigen Wahrscheinlichkeiten aus der Vergangenheit, keine Garantien.</p>
</div></section>
<script>{JS}</script></body></html>"""


def mvrv_chart(mv: pd.Series, w=300, h=90):
    lo, hi = 0.0, max(4.5, float(mv.max()))
    y = lambda v: h - (v - lo) / (hi - lo) * h
    pts = " ".join(f"{i * w / (len(mv) - 1):.1f},{y(v):.1f}" for i, v in enumerate(mv))
    years = sorted({d.year for d in mv.index if d.year % 4 == 1})
    ticks = ""
    for yr in years:
        idx = mv.index.searchsorted(pd.Timestamp(f"{yr}-01-01"))
        x = idx * w / (len(mv) - 1)
        ticks += f'<text x="{x:.0f}" y="{h + 12}" class="ax">{yr}</text>'
    return f"""<svg class="mvrv" viewBox="0 -4 {w} {h + 18}">
<rect x="0" y="{y(hi):.1f}" width="{w}" height="{y(3) - y(hi):.1f}" class="zone-bad"/>
<rect x="0" y="{y(1):.1f}" width="{w}" height="{h - y(1):.1f}" class="zone-good"/>
<polyline points="{pts}" fill="none" stroke="var(--fg)" stroke-width="1.2"/>
<text x="2" y="{y(3) - 3:.0f}" class="ax">3</text><text x="2" y="{y(1) - 3:.0f}" class="ax">1</text>{ticks}</svg>"""


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#6b7280;--line:#e5e7eb;
--good:#15803d;--good-bg:#dcfce7;--bad:#b91c1c;--bad-bg:#fee2e2;--mid:#92400e;--mid-bg:#fef3c7;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8eb;--muted:#9aa3ae;--line:#2a2f3a;
--good:#4ade80;--good-bg:#14301f;--bad:#f87171;--bad-bg:#3a1717;--mid:#fbbf24;--mid-bg:#3a2e10;--accent:#60a5fa}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px 48px;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:1400px;margin-inline:auto}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;margin-bottom:8px}
h1{margin:0;font-size:26px}h2{font-size:18px;margin:28px 0 10px}h3{font-size:14px;margin:0 0 10px;font-weight:600}
.muted{color:var(--muted)}.legend{display:flex;gap:6px;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:12px}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}.grid3 .card{margin:0}
.scroll{overflow-x:auto}
table.data{border-collapse:collapse;width:100%;white-space:nowrap}
.data th{font-size:12px;font-weight:600;color:var(--muted);text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:bottom}
.data th.grp{text-align:center;border-bottom:1px dashed var(--line)}
.sortable th[data-col]{cursor:pointer;user-select:none}.sortable th[data-col]:hover{color:var(--fg)}
.data td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
.data tr:last-child td{border-bottom:0}.data tr.idx td{background:color-mix(in srgb,var(--line) 30%,transparent)}
.data tr.cur td{font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}.up{color:var(--good)}.down{color:var(--bad)}
.sub{font-size:11px;color:var(--muted);font-weight:400}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-weight:600;font-size:12px;font-variant-numeric:tabular-nums}
.pill.good{background:var(--good-bg);color:var(--good)}.pill.bad{background:var(--bad-bg);color:var(--bad)}.pill.mid{background:var(--mid-bg);color:var(--mid)}
.pill.lg{font-size:22px;padding:4px 14px}
.big{display:flex;align-items:center;gap:10px;margin-bottom:8px}.lbl{color:var(--muted)}
.spark{width:120px;height:32px;display:block}.spark.wide{width:100%;height:40px;margin-top:8px}
.bar{height:4px;background:var(--line);border-radius:2px;margin-top:4px;width:70px;margin-left:auto}
.bar span{display:block;height:100%;background:var(--bad);border-radius:2px;margin-left:auto}
.gauge{position:relative;height:8px;border-radius:4px;background:linear-gradient(90deg,var(--good),var(--mid) 50%,var(--bad));margin:10px 0 4px}
.gauge span{position:absolute;top:-4px;width:4px;height:16px;background:var(--fg);border-radius:2px;transform:translateX(-50%)}
.gscale{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
.mini{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:12px;margin-top:10px;color:var(--muted)}.mini b{color:var(--fg);margin-left:4px}
.note{font-size:12px;color:var(--muted);margin:8px 0 0}
.ev{font-size:10px;font-weight:600;padding:1px 6px;border-radius:999px;border:1px solid currentColor;margin-left:4px;white-space:nowrap}
.ev-s{color:var(--good)}.ev-m{color:var(--mid)}.ev-w{color:var(--muted)}
.tag{font-size:10px;font-weight:500;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.z{opacity:.75}
.mvrv{width:100%;height:auto;margin-top:10px}.mvrv .ax{font-size:9px;fill:var(--muted)}
.zone-good{fill:var(--good-bg)}.zone-bad{fill:var(--bad-bg)}
.explain dl{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;margin:0}
.explain dt{font-weight:600}.explain dd{margin:0;color:var(--muted)}
@media (max-width:600px){.explain dl{grid-template-columns:1fr}.explain dd{margin-bottom:8px}}
"""

JS = """
document.querySelectorAll('table.sortable th[data-col]').forEach(th=>{
  th.addEventListener('click',()=>{
    const t=th.closest('table'), col=+th.dataset.col;
    const dir=th.dataset.dir==='desc'?'asc':'desc'; th.dataset.dir=dir;
    const val=r=>{const c=r.cells[col]; if(!c) return -1e9; const v=c.dataset.v;
      return v!==undefined?parseFloat(v):c.textContent.trim().toLowerCase();};
    const rows=[...t.tBodies[0].rows];
    rows.sort((a,b)=>{const x=val(a),y=val(b);const r=x>y?1:x<y?-1:0;return dir==='asc'?r:-r;});
    rows.forEach(r=>t.tBodies[0].appendChild(r));
  });
});
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    print("Lade Kurse ...")
    crypto = {t: analyze(s) for t, s in load_prices(cfg.CRYPTO).items()}
    stocks = {t: analyze(s) for t, s in load_prices({**cfg.STOCKS, **cfg.INDICES}).items()}
    print("Lade Firmenkennzahlen ...")
    fund = {t: fundamentals(t) for t in cfg.STOCKS}
    print("Lade Stimmung, Shiller-KGV und Bitcoin-On-Chain-Daten ...")
    page = build(crypto, stocks, fund, crypto_fng(), stock_fng(), shiller_cape(), btc_onchain())
    with open("radar.html", "w", encoding="utf-8") as f:
        f.write(page)
    print("Fertig: radar.html")
    if not args.no_open:
        subprocess.run(["open", "radar.html"])


if __name__ == "__main__":
    main()
