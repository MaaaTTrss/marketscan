"""Morgenbericht als Bild (PNG) – für Telegram und die Cloud-Seite.

Aufbau (Hochformat, fürs Handy):
  Kopf · Fear & Greed als zwei Tachos · Kachel-Heatmap aller Werte (Tagesänderung)
  · Signale · Bitcoin-Zyklus · Fußzeile
Farben wie im Radar: grün = spricht eher für Kauf / steigt, rot = Vorsicht / fällt.
"""
import math
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Wedge, Circle  # noqa: E402

# Farben (dunkles Design wie der Radar)
BG, CARD, LINE = "#0f1115", "#171a21", "#2a2f3a"
FG, MUTED = "#e6e8eb", "#9aa3ae"
GOOD, BAD, INFO = "#15803d", "#b91c1c", "#2563eb"
GOOD_T, BAD_T, INFO_T = "#4ade80", "#f87171", "#60a5fa"
TILE0 = "#232733"            # Kachel bei 0 %
CAP = 0.05                   # ab ±5 % volle Farbe
GROUPS = [("crypto", "Krypto"), ("stock", "Aktien"), ("etf", "ETFs & Indizes")]
FNG_DE = {"extreme fear": "extreme Angst", "fear": "Angst", "neutral": "neutral",
          "greed": "Gier", "extreme greed": "extreme Gier"}
DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

W = 5.4                      # Breite in Zoll (x 200 dpi = 1080 px)
PAD = 0.28


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def pct(v):
    if abs(v) < 0.0005:
        return "±0,0 %"
    return f"{v * 100:+.1f} %".replace(".", ",").replace("-", "−")


def fmt_price(v):
    if v is None:
        return ""
    d = 0 if v >= 1000 else 2 if v >= 1 else 4
    return f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".") + " $"


def card(ax, x, y, w, h):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12",
                                fc=CARD, ec=LINE, lw=0.8))


def gauge(ax, cx, cy, r, value, title, label, prev, prev_label="Vortag"):
    # Skala 0..100 als Halbkreis (oben), links grün (Angst = eher Kaufgelegenheit), rechts rot (Gier).
    # Achtung: y-Achse zeigt nach unten, darum laufen die Winkel 180 -> 360.
    steps = 60
    for i in range(steps):
        t0, t1 = i / steps, (i + 1) / steps
        col = mix(GOOD, "#a16207", t0 * 2) if t0 < 0.5 else mix("#a16207", BAD, (t0 - 0.5) * 2)
        ax.add_patch(Wedge((cx, cy), r, 180 + t0 * 180, 180 + t1 * 180 + 0.4, width=r * 0.2, fc=col, ec="none"))
    ax.text(cx, cy - r - 0.1, title, ha="center", va="bottom", color=MUTED, fontsize=8.5)
    if value is not None:
        a = math.radians(180 + value * 1.8)
        mx, my = cx + (r * 0.9) * math.cos(a), cy + (r * 0.9) * math.sin(a)
        ax.add_patch(Circle((mx, my), r * 0.12, fc=FG, ec=CARD, lw=2.5))
        ax.text(cx, cy - 0.02, f"{value}", ha="center", va="bottom", color=FG, fontsize=22, fontweight="bold")
    ax.text(cx, cy + 0.06, label, ha="center", va="top", color=FG, fontsize=9.5, fontweight="bold")
    if prev is not None:
        ax.text(cx, cy + 0.3, f"{prev_label} {prev}", ha="center", va="top", color=MUTED, fontsize=7.5)


def money(v):
    """Betrag mit Vorzeichen, 2 Nachkommastellen unter 1.000."""
    d = 0 if abs(v) >= 1000 else 2
    t = f"{abs(v):,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("−" if v < 0 else "+") + t + " $"


def sparkline(ax, vals, x0, y0, w, h):
    vals = [v for v in vals if v is not None]
    if len(vals) < 3:
        return
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    xs = [x0 + i / (len(vals) - 1) * w for i in range(len(vals))]
    ys = [y0 + h - (v - lo) / rng * h for v in vals]
    ax.plot(xs, ys, color=FG, alpha=0.85, lw=1.1, solid_capstyle="round", solid_joinstyle="round")
    ax.add_patch(Circle((xs[-1], ys[-1]), 0.025, fc=FG, ec="none"))


def render(data, now, path, depot=None, week=None):
    """depot = private_store.summary(...) (nur fürs private Bild); week = {"log": [...]} -> Wochenrückblick."""
    m, items = data["market"], [a for a in data["items"] if not a.get("error")]
    cur = [c for c in data["alerts"]["current"]]
    field = "d7" if week else "d1"
    cap = 0.12 if week else CAP
    if week:
        sig = [e for e in week.get("log", []) if e.get("kind") != "info"][:8]
    else:
        sig = sorted([c for c in cur if c["kind"] in ("good", "bad", "alarm")],
                     key=lambda c: ({"alarm": 0, "good": 1, "bad": 2}[c["kind"]], c["prio"]))[:7]
    infos = [c for c in cur if c["kind"] == "info" and not week]

    today = now.date()
    horizon = 14 if week else 7
    termine = sorted([a for a in items if a.get("earnings")
                      and 0 <= (datetime.strptime(a["earnings"], "%Y-%m-%d").date() - today).days <= horizon],
                     key=lambda a: a["earnings"])[:6]
    dep_rows = (depot or {}).get("rows", [])[:6] if depot else []

    cols = 3
    tile_h, gap = 0.8, 0.08
    groups = [(k, t, [a for a in items if a.get("group") == k]) for k, t in GROUPS]
    groups = [g for g in groups if g[2]]
    dep_h = (1.25 + len(dep_rows) * 0.27 + 0.15) if dep_rows else 0
    heat_h = sum(0.34 + math.ceil(len(g[2]) / cols) * (tile_h + gap) for g in groups) + 0.2
    sig_h = (0.4 if week else 0.85) + max(1, len(sig)) * 0.34 + (0.34 if infos else 0) + 0.15
    ter_h = (0.55 + len(termine) * 0.27 + 0.15) if termine else 0
    btc = m.get("btc") or {}
    btc_h = 1.6 if btc.get("mvrv") is not None else 0
    H = 1.05 + dep_h + 2.05 + heat_h + sig_h + ter_h + btc_h + 0.55

    fig = plt.figure(figsize=(W, H), dpi=200, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    plt.rcParams["font.family"] = "DejaVu Sans"

    # Kopf
    y = PAD
    t1 = ax.text(PAD, y + 0.05, "Markt", color="#22c55e", fontsize=17, fontweight="bold", va="top")
    bb = t1.get_window_extent(renderer=fig.canvas.get_renderer())
    x1 = ax.transData.inverted().transform((bb.x1, 0))[0]
    ax.text(x1, y + 0.05, "·Radar", color="#3b82f6", fontsize=17, fontweight="bold", va="top")
    ax.text(W - PAD, y + 0.02, "Wochenrückblick" if week else "Morgenbericht", color=FG, fontsize=10,
            fontweight="bold", va="top", ha="right")
    if week:
        start = now - timedelta(days=6)
        sub = f"{start.strftime('%d.%m.')} – {now.strftime('%d.%m.%Y')}"
    else:
        sub = f"{DAYS[now.weekday()]}, {now.strftime('%d.%m.%Y · %H:%M')} Uhr"
    ax.text(W - PAD, y + 0.27, sub, color=MUTED, fontsize=8, va="top", ha="right")
    y = 1.05

    # Depot (nur im privaten Bild)
    if dep_rows:
        card(ax, PAD, y, W - 2 * PAD, dep_h - 0.15)
        ax.text(PAD + 0.18, y + 0.14, "Mein Depot", color=FG, fontsize=10, fontweight="bold", va="top")
        per, per_pct = (depot["week"], depot["week_pct"]) if week else (depot["day"], depot["day_pct"])
        kp = [("Wert", fmt_price(depot["value"]), FG),
              ("Diese Woche" if week else "Heute", money(per) + (f"  ({pct(per_pct)})" if per_pct is not None else ""),
               GOOD_T if per >= 0 else BAD_T),
              ("Gesamt", money(depot["pl"]) + (f"  ({pct(depot['pl_pct'])})" if depot["pl_pct"] is not None else ""),
               GOOD_T if depot["pl"] >= 0 else BAD_T)]
        for i, (lab, val, col) in enumerate(kp):
            x0 = PAD + 0.18 + i * 1.6
            ax.text(x0, y + 0.5, lab, color=MUTED, fontsize=7, va="top")
            ax.text(x0, y + 0.68, val, color=col, fontsize=9 if i else 11, fontweight="bold", va="top")
        yy = y + 1.2
        ax.text(W - PAD - 1.3, yy - 0.2, "Woche" if week else "Heute", color=MUTED, fontsize=6.5, ha="right", va="center")
        ax.text(W - PAD - 0.18, yy - 0.2, "seit Kauf", color=MUTED, fontsize=6.5, ha="right", va="center")
        for r in dep_rows:
            ch = r.get(field)
            ax.text(PAD + 0.18, yy, r["name"][:16], color=FG, fontsize=8, fontweight="bold", va="center")
            ax.text(PAD + 2.2, yy, fmt_price(r.get("value")), color=FG, fontsize=8, va="center", ha="right")
            if ch is not None:
                ax.text(W - PAD - 1.3, yy, pct(ch), color=GOOD_T if ch >= 0 else BAD_T, fontsize=8, va="center", ha="right")
            if r.get("pl_pct") is not None:
                ax.text(W - PAD - 0.18, yy, pct(r["pl_pct"]), color=GOOD_T if r["pl_pct"] >= 0 else BAD_T,
                        fontsize=8, fontweight="bold", va="center", ha="right")
            yy += 0.27
        y += dep_h

    # Fear & Greed
    card(ax, PAD, y, W - 2 * PAD, 1.85)
    ax.text(PAD + 0.18, y + 0.14, "Stimmung · Fear & Greed", color=FG, fontsize=9.5, fontweight="bold", va="top")
    ax.text(W - PAD - 0.18, y + 0.15, "0 = Panik   100 = Gier", color=MUTED, fontsize=7, va="top", ha="right")
    for i, (key, title) in enumerate((("crypto_fng", "Krypto"), ("stock_fng", "Aktien"))):
        f = m.get(key) or {}
        lab = FNG_DE.get(str(f.get("label", "")).lower(), str(f.get("label", "")))
        gauge(ax, W / 4 + i * W / 2, y + 1.3, 0.68, f.get("now"), title, lab,
              f.get("w1") if week else f.get("d1"), "vor 1 Woche" if week else "Vortag")
    y += 2.05

    # Heatmap
    ax.text(PAD, y, "Wochenänderung" if week else "Tagesänderung", color=FG, fontsize=10, fontweight="bold", va="top")
    ax.text(W - PAD, y + 0.02, f"grün steigt · rot fällt · Linie = 30 Tage", color=MUTED, fontsize=7, va="top", ha="right")
    y += 0.34
    tw = (W - 2 * PAD - (cols - 1) * gap) / cols
    for key, title, grp in groups:
        ax.text(PAD, y, title.upper(), color=MUTED, fontsize=7, fontweight="bold", va="top")
        y += 0.2
        grp = sorted(grp, key=lambda a: -(a.get(field) or 0))
        for i, a in enumerate(grp):
            r, c = divmod(i, cols)
            x0, y0 = PAD + c * (tw + gap), y + r * (tile_h + gap)
            d = a.get(field)
            t = 0 if d is None else min(1, abs(d) / cap)
            col = TILE0 if d is None else mix(TILE0, GOOD if d >= 0 else BAD, 0.25 + 0.75 * t if abs(d) >= 0.0005 else 0)
            ax.add_patch(FancyBboxPatch((x0, y0), tw, tile_h, boxstyle="round,pad=0,rounding_size=0.07",
                                        fc=col, ec=BG, lw=1.5))
            name = a["name"] if len(a["name"]) <= 14 else a["name"][:13] + "…"
            ax.text(x0 + 0.1, y0 + 0.1, name, color=FG, fontsize=7.8, fontweight="bold", va="top")
            ax.text(x0 + 0.1, y0 + 0.3, fmt_price(a.get("price")), color=FG, alpha=0.8, fontsize=7.6, va="top")
            sparkline(ax, (a.get("spark") or [])[-11:], x0 + tw * 0.6, y0 + 0.3, tw * 0.4 - 0.1, 0.24)
            ax.text(x0 + 0.1, y0 + tile_h - 0.08, "–" if d is None else pct(d), color=FG, fontsize=10.5,
                    fontweight="bold", va="bottom")
            if a.get("rsi") is not None:
                ax.text(x0 + tw - 0.08, y0 + tile_h - 0.09, f"RSI {a['rsi']:.0f}", color=FG, alpha=0.75,
                        fontsize=6.3, va="bottom", ha="right")
        y += math.ceil(len(grp) / cols) * (tile_h + gap) + 0.14
    y += 0.06

    # Signale
    COL = {"good": GOOD_T, "bad": BAD_T, "alarm": "#fbbf24", "info": INFO_T}
    card(ax, PAD, y, W - 2 * PAD, sig_h - 0.15)
    ax.text(PAD + 0.18, y + 0.14, "Meldungen dieser Woche" if week else "Signale", color=FG, fontsize=10,
            fontweight="bold", va="top")
    if week:
        yy = y + 0.55
    else:
        ax.text(PAD + 0.18, y + 0.42, "überverkauft = stark gefallen (RSI unter 30) – oft folgt eine Gegenbewegung nach oben",
                color=MUTED, fontsize=6.5, va="top")
        ax.text(PAD + 0.18, y + 0.58, "★ = zusätzlich passender Trend – das stärkere Signal",
                color=MUTED, fontsize=6.5, va="top")
        yy = y + 0.92
    if not sig:
        ax.text(PAD + 0.18, yy, "Keine Kauf- oder Vorsicht-Signale." if not week else "Keine neuen Meldungen diese Woche.",
                color=MUTED, fontsize=8.5, va="center")
        yy += 0.34
    for c in sig:
        col = COL.get(c["kind"], FG)
        ax.add_patch(Circle((PAD + 0.27, yy), 0.06, fc=col, ec="none"))
        nm = c["name"] if len(c["name"]) <= 13 else c["name"][:12] + "…"
        ax.text(PAD + 0.42, yy, nm, color=FG, fontsize=8.6, fontweight="bold", va="center")
        lim = 34 if week else 40
        txt = c["text"] if len(c["text"]) <= lim else c["text"][:lim - 1] + "…"
        ax.text(PAD + 1.75, yy, txt, color=col, fontsize=8, va="center")
        if week and c.get("time"):
            ax.text(W - PAD - 0.18, yy, c["time"].split()[0], color=MUTED, fontsize=7, va="center", ha="right")
        yy += 0.34
    if infos:
        ax.add_patch(Circle((PAD + 0.27, yy), 0.06, fc=INFO_T, ec="none"))
        ax.text(PAD + 0.42, yy, f"{len(infos)} weitere Hinweise – Details im Radar", color=MUTED, fontsize=7.8, va="center")
    y += sig_h

    # Termine
    if termine:
        card(ax, PAD, y, W - 2 * PAD, ter_h - 0.15)
        ax.text(PAD + 0.18, y + 0.14, "Termine · Quartalszahlen", color=FG, fontsize=10, fontweight="bold", va="top")
        yy = y + 0.55
        for a in termine:
            dd = datetime.strptime(a["earnings"], "%Y-%m-%d").date()
            n = (dd - today).days
            when = "heute" if n == 0 else "morgen" if n == 1 else f"in {n} Tagen"
            ax.text(PAD + 0.18, yy, a["name"], color=FG, fontsize=8.5, fontweight="bold", va="center")
            ax.text(PAD + 2.0, yy, f"{DAYS[dd.weekday()]}, {dd.strftime('%d.%m.')}", color=FG, fontsize=8.5, va="center")
            ax.text(W - PAD - 0.18, yy, when, color="#fbbf24" if n <= 2 else MUTED, fontsize=7.8, va="center", ha="right")
            yy += 0.27
        y += ter_h

    # Bitcoin-Zyklus
    if btc_h:
        card(ax, PAD, y, W - 2 * PAD, btc_h - 0.15)
        ax.text(PAD + 0.18, y + 0.14, "Bitcoin-Zyklus", color=FG, fontsize=10, fontweight="bold", va="top")
        vals = [("MVRV", btc.get("mvrv"), 1, 3, "günstig", "teuer"),
                ("Mayer", btc.get("mayer"), 0.8, 2.4, "günstig", "überhitzt")]
        for i, (lab, v, lo, hi, low_w, high_w) in enumerate(vals):
            if v is None:
                continue
            x0 = PAD + 0.18 + i * 2.15
            col = GOOD_T if v < lo else BAD_T if v > hi else FG
            word = low_w if v < lo else high_w if v > hi else "neutral"
            ax.text(x0, y + 0.47, lab, color=MUTED, fontsize=7.5, va="top")
            ax.text(x0 + 0.5, y + 0.42, f"{v:.2f}".replace(".", ","), color=col, fontsize=12, fontweight="bold", va="top")
            ax.text(x0 + 1.05, y + 0.47, word, color=col, fontsize=7.5, va="top")
        if btc.get("days") is not None:
            ax.text(W - PAD - 0.18, y + 0.14, f"{btc['days']} Tage seit Halving", color=MUTED, fontsize=7.5,
                    va="top", ha="right")
        notes = ["MVRV: Kurs ÷ Ø Einkaufspreis aller Bitcoin. Unter 1 = historisch günstig, über 3 = teuer.",
                 "Mayer: Kurs ÷ 200-Tage-Schnitt. Unter 0,8 = günstig, über 2,4 = überhitzt.",
                 "Halving alle ~4 Jahre. Hochs kamen bisher meist 12–18 Monate danach."]
        for k, t in enumerate(notes):
            ax.text(PAD + 0.18, y + 0.85 + k * 0.18, t, color=MUTED, fontsize=6.5, va="top")
        y += btc_h

    ax.text(W / 2, H - 0.25, "Hinweise zum Genauer-Hinschauen – keine Anlageberatung.",
            color=MUTED, fontsize=6.8, ha="center", va="center")
    fig.savefig(path, dpi=200, facecolor=BG)
    plt.close(fig)
    return path


if __name__ == "__main__":   # Vorschau: python report_image.py site/data/data.json out.png
    import json
    import sys
    d = json.load(open(sys.argv[1]))
    demo = {"rows": [{"name": "Bitcoin", "value": 3628.6, "d1": -0.019, "d7": -0.044, "pl_pct": 0.05},
                     {"name": "Nvidia", "value": 1738.5, "d1": 0.029, "d7": -0.131, "pl_pct": -0.08}],
            "value": 5367.1, "day": -20.3, "day_pct": -0.0038, "week": -390.0, "week_pct": -0.068, "pl": -28.0, "pl_pct": -0.005}
    mode = sys.argv[3] if len(sys.argv) > 3 else "day"
    render(d, datetime.now(), sys.argv[2], depot=demo if "depot" in mode else None,
           week={"log": d["alerts"]["log"]} if "week" in mode else None)
