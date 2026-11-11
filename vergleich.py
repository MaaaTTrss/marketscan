"""Strategie-Vergleich (Backtest): Wie hätten die Radar-Signale in der Vergangenheit
abgeschnitten?

Zwei Varianten, jeweils mit echten Tageskursen:
  A) Einmalanlage: 10.000 $ am Start, Strategie entscheidet täglich "investiert" oder "Cash".
  B) Sparplan: jeden Monat 500 $, Strategie entscheidet, WANN das Geld investiert wird.

Regeln gegen Selbstbetrug:
  - Das Signal von heute (Schlusskurs) gilt erst ab morgen -> kein Blick in die Zukunft.
  - Jeder Kauf/Verkauf kostet Gebühren (Krypto 0,1 %, Aktien/ETFs 0,05 %).
  - Alle Schwellen sind die aus dem Radar, NICHT im Nachhinein optimiert.

Aufruf:  .venv/bin/python vergleich.py BTC-USD NVDA SPY
"""
import warnings

warnings.filterwarnings("ignore")
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import radar

START_CAPITAL = 10_000
MONTHLY = 500
CRYPTO_START = "2018-02-01"   # ab hier gibt es Fear-&-Greed-Daten
STOCK_START = "2015-01-01"

_cache = {}


# ---------------------------------------------------------------- Daten
def load_price(symbol):
    # Ticker.history ist – anders als yf.download – sicher, wenn der Radar parallel Kurse lädt
    s = yf.Ticker(symbol).history(period="max", auto_adjust=True)["Close"].dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s[~s.index.duplicated(keep="last")]


def load_fng():
    if "fng" not in _cache:
        d = requests.get("https://api.alternative.me/fng/?limit=0&date_format=world", timeout=20).json()["data"]
        s = pd.Series({pd.to_datetime(x["timestamp"], format="%d-%m-%Y"): int(x["value"]) for x in d}).sort_index()
        _cache["fng"] = s
    return _cache["fng"]


def load_onchain():
    if "onchain" not in _cache:
        _cache["onchain"] = radar.btc_onchain()
    return _cache["onchain"]


def rsi_series(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


# ---------------------------------------------------------------- Hilfen
def state_machine(enter, exit_, hold_days=None):
    pos, cur, timer = np.zeros(len(enter)), 0, 0
    for i in range(len(enter)):
        if cur == 0 and enter[i]:
            cur, timer = 1, (hold_days or 0)
        elif cur == 1 and exit_[i]:
            cur = 0
        pos[i] = cur
    return pos


def xirr(flows):
    """Jährliche Rendite für unregelmäßige Ein-/Auszahlungen [(datum, betrag)]."""
    t0 = flows[0][0]
    ts = np.array([(d - t0).days / 365.25 for d, _ in flows])
    cf = np.array([v for _, v in flows])
    lo, hi = -0.99, 10.0
    f = lambda r: np.sum(cf / (1 + r) ** ts)
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def curve(eq):
    w = eq.resample("W").last().dropna()
    return [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in w.items()]


def lump_stats(eq, pos, price, cost):
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    trades, wins, entry = 0, 0, None
    p = pd.Series(pos, index=eq.index)
    for i in range(len(p)):
        if p.iloc[i] == 1 and (i == 0 or p.iloc[i - 1] == 0):
            trades += 1
            entry = price.iloc[i]
        elif p.iloc[i] == 0 and i > 0 and p.iloc[i - 1] == 1 and entry is not None:
            wins += price.iloc[i] > entry * (1 + 2 * cost)
            entry = None
    closed = trades - (1 if p.iloc[-1] == 1 else 0)
    return {"final": float(eq.iloc[-1]),
            "cagr": float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1),
            "maxdd": float((eq / eq.cummax() - 1).min()),
            "trades": int(trades),
            "invested": float(p.mean()),
            "winrate": (wins / closed) if closed > 0 else None}


# ---------------------------------------------------------------- Strategien
def run(symbol, group=None):
    group = group or ("crypto" if symbol.endswith("-USD") else "stock")
    is_crypto = group == "crypto"
    cost = 0.001 if is_crypto else 0.0005

    full = load_price(symbol)
    if len(full) < 400:
        raise ValueError("Zu wenig Kurshistorie für einen sinnvollen Test (mind. ~1,5 Jahre).")
    sma50, sma200 = full.rolling(50).mean(), full.rolling(200).mean()
    rsi = rsi_series(full)
    dd = full / full.cummax() - 1

    first_ok = full.index[200]
    start = max(first_ok, pd.Timestamp(CRYPTO_START if is_crypto else STOCK_START))
    s = full[start:]
    idx = s.index
    ret = s.pct_change().fillna(0)
    ind = pd.DataFrame({"sma50": sma50, "sma200": sma200, "rsi": rsi, "dd": dd}).loc[idx]
    fng = load_fng().reindex(idx, method="ffill") if is_crypto else None
    onchain = None
    if symbol == "BTC-USD":
        oc = load_onchain()
        if oc is not None:
            mayer = oc["PriceUSD"] / oc["PriceUSD"].rolling(200).mean()
            onchain = pd.DataFrame({"mvrv": oc["CapMVRVCur"], "mayer": mayer}).reindex(idx, method="ffill")

    trend_up = ((s > ind.sma200) & (ind.sma50 > ind.sma200)).values
    panic_extra = (fng <= 25).values if is_crypto else (ind.dd <= -0.2).values

    strategies = [
        ("hold", "Kaufen & Halten", "Am Start alles kaufen, nie verkaufen (Vergleichsmaßstab).",
         np.ones(len(s))),
        ("trend", "Trend", "Investiert, solange Kurs über 200-Tage-Schnitt und 50- über 200-Tage-Schnitt.",
         trend_up.astype(float)),
        ("rsi", "RSI 30/70", "Kaufen, wenn RSI unter 30; verkaufen, wenn RSI über 70.",
         state_machine((ind.rsi < 30).values, (ind.rsi > 70).values)),
        ("dip", "Dip abwarten", "Mit Cash warten, bis der Kurs 30 % unter dem Hoch ist – dann kaufen und halten.",
         state_machine((ind.dd <= -0.3).values, np.zeros(len(s), bool))),
    ]
    if is_crypto:
        strategies.append(("fng", "Fear & Greed", "Kaufen bei extremer Angst (≤ 25), verkaufen bei extremer Gier (≥ 75).",
                           state_machine((fng <= 25).values, (fng >= 75).values)))
    if onchain is not None:
        strategies.append(("onchain", "MVRV / Mayer", "Kaufen, wenn MVRV < 1 oder Mayer < 0,8; verkaufen, wenn MVRV > 3 oder Mayer > 2,4.",
                           state_machine(((onchain.mvrv < 1) | (onchain.mayer < 0.8)).values,
                                         ((onchain.mvrv > 3) | (onchain.mayer > 2.4)).values)))
    # Kombi: Trend-Filter + Panik-Käufe (RSI < 30 UND Angst bzw. 20 % unter Hoch), die 90 Tage gehalten werden
    pos, timer = np.zeros(len(s)), 0
    panic = (ind.rsi < 30).values & panic_extra
    for i in range(len(s)):
        if panic[i]:
            timer = 90
        pos[i] = 1 if (trend_up[i] or timer > 0) else 0
        timer = max(0, timer - 1)
    strategies.append(("kombi", "Kombi", "Investiert im Aufwärtstrend; zusätzlich Panik-Käufe (RSI < 30 + "
                       + ("extreme Angst" if is_crypto else "20 % unter Hoch") + "), 90 Tage gehalten.", pos))

    lump = []
    for key, name, desc, p in strategies:
        p = pd.Series(p, index=idx)
        sr = p.shift(1).fillna(0) * ret - p.diff().abs().fillna(p.iloc[0]) * cost
        eq = START_CAPITAL * (1 + sr).cumprod()
        lump.append({"key": key, "name": name, "desc": desc, **lump_stats(eq, p.values, s, cost),
                     "curve": curve(eq)})

    # ---- Sparplan-Varianten: jeden Monat 500 $
    month_first = ~pd.Series(idx.to_period("M"), index=idx).duplicated()
    trig = {"sparplan": np.ones(len(s), bool),
            "dip_sparer": (ind.dd <= -0.3).values,
            "rsi_sparer": (ind.rsi < 30).values}
    names = {"sparplan": ("Sparplan", "Jeden Monat sofort 500 $ kaufen."),
             "dip_sparer": ("Dip-Sparer", "500 $/Monat ansparen, nur kaufen, wenn 30 % unter dem Hoch."),
             "rsi_sparer": ("RSI-Sparer", "500 $/Monat ansparen, nur kaufen, wenn RSI unter 30.")}
    if is_crypto:
        trig["angst_sparer"] = (fng <= 25).values
        names["angst_sparer"] = ("Angst-Sparer", "500 $/Monat ansparen, nur bei extremer Angst (≤ 25) kaufen.")
    saving = []
    prices = s.values
    for key, t in trig.items():
        cash = units = paid = 0.0
        flows, values, cash_share = [], [], []
        for i, d in enumerate(idx):
            if month_first.iloc[i]:
                cash += MONTHLY
                paid += MONTHLY
                flows.append((d, -MONTHLY))
            if t[i] and cash > 0:
                units += cash * (1 - cost) / prices[i]
                cash = 0.0
            v = cash + units * prices[i]
            values.append(v)
            cash_share.append(cash / v if v else 0)
        final = values[-1]
        flows.append((idx[-1], final))
        val = pd.Series(values, index=idx)
        saving.append({"key": key, "name": names[key][0], "desc": names[key][1],
                       "paid": paid, "final": final, "multiple": final / paid,
                       "irr": xirr(flows), "cash_avg": float(np.mean(cash_share)),
                       "cash_end": cash, "curve": curve(val)})

    return {"symbol": symbol, "start": str(idx[0].date()), "end": str(idx[-1].date()),
            "years": round((idx[-1] - idx[0]).days / 365.25, 1),
            "lump": lump, "saving": saving,
            "notes": ("Krypto-Test beginnt 2018, weil es erst ab da Fear-&-Greed-Daten gibt."
                      if is_crypto else "")}


# ---------------------------------------------------------------- Kommandozeile
def fmt_money(v):
    return f"{v:>12,.0f} $".replace(",", ".")


def main(symbols):
    for sym in symbols:
        try:
            r = run(sym)
        except Exception as e:
            print(f"\n{sym}: {e}")
            continue
        print(f"\n=== {sym}  ({r['start']} bis {r['end']}, {r['years']} Jahre) ===")
        print(f"{'Einmalanlage 10.000 $':<22}{'Endwert':>15}{'p.a.':>8}{'max.Verl.':>10}{'Trades':>7}{'investiert':>11}{'Treffer':>8}")
        for x in sorted(r["lump"], key=lambda x: -x["final"]):
            wr = f"{x['winrate']:.0%}" if x["winrate"] is not None else "–"
            print(f"{x['name']:<22}{fmt_money(x['final']):>15}{x['cagr']:>8.1%}{x['maxdd']:>10.0%}"
                  f"{x['trades']:>7}{x['invested']:>11.0%}{wr:>8}")
        print(f"{'Sparplan 500 $/Monat':<22}{'Endwert':>15}{'p.a.':>8}{'× Einz.':>10}{'Ø Cash':>9}")
        for x in sorted(r["saving"], key=lambda x: -x["final"]):
            irr = f"{x['irr']:.1%}" if x["irr"] is not None else "–"
            print(f"{x['name']:<22}{fmt_money(x['final']):>15}{irr:>8}{x['multiple']:>10.2f}{x['cash_avg']:>9.0%}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["BTC-USD", "ETH-USD", "SOL-USD", "NVDA", "AAPL", "MSFT", "TSLA", "SPY", "QQQ"])
