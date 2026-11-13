# Trading-Bot (Paper Money)

Ein einfacher, ehrlicher Trendfolge-Bot, der mit **Spielgeld** über das
Paper-Trading-Konto von [Alpaca](https://alpaca.markets) handelt.
`paper=True` ist im Code fest eingebaut – er kann kein echtes Geld bewegen.

## Was er macht

1. Schaut sich einmal am Tag 6 breite ETFs an (S&P 500, Nasdaq, Nebenwerte,
   Europa/Japan, Anleihen, Gold).
2. Ist ein Wert im **Aufwärtstrend** (Kurs über 200-Tage-Schnitt und
   50-Tage-Schnitt über 200-Tage-Schnitt), wird er gehalten.
3. Das Geld wird gleichmäßig auf alle Werte im Trend verteilt
   (max. 25 % pro Wert). Alles andere bleibt in Cash.

## Backtest-Ergebnis (2010–2026, 100.000 $ Start)

| Strategie              | Endwert   | Rendite p.a. | Größter Verlust |
|------------------------|-----------|--------------|-----------------|
| Trend-Bot              | 380.676 $ | 8,8 %        | −15,0 %         |
| Kaufen & Halten (alle) | 502.171 $ | 10,7 %       | −26,0 %         |
| Nur S&P 500            | 842.823 $ | 14,3 %       | −33,7 %         |

**Ehrlich gesagt:** Der Bot schlägt einfaches Kaufen & Halten des S&P 500
*nicht*. Sein Vorteil sind die deutlich kleineren Verluste in Crashs.
Vergangene Ergebnisse garantieren nichts für die Zukunft.

## Einrichtung

1. Auf alpaca.markets kostenlos registrieren (Paper-Trading geht nur mit E-Mail).
2. Im Dashboard oben auf **Paper** umschalten → *API Keys* → *Generate*.
3. `.env.example` nach `.env` kopieren und beide Schlüssel eintragen.

## Benutzung

```bash
.venv/bin/python backtest.py        # Strategie auf alten Daten testen (keine Keys nötig)
.venv/bin/python bot.py             # Vorschau: was würde der Bot heute tun?
.venv/bin/python bot.py --trade     # Orders aufs Paper-Konto senden
```

Einmal pro Handelstag ausführen reicht, z. B. nachmittags deutscher Zeit
(US-Börse öffnet 15:30 Uhr MEZ). Alle Orders landen in `trades.csv`.

Neu installieren (falls `.venv` fehlt):
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Anpassen

Alle Stellschrauben stehen in `config.py` (Symbole, Durchschnitte, Risiko).
Nach jeder Änderung erst `backtest.py` laufen lassen, dann `bot.py`.

## MarketScan (live)

**Starten:** Doppelklick auf `Radar starten.command` – oder im Terminal:

```bash
.venv/bin/python app.py
```

Der Radar öffnet sich unter http://127.0.0.1:8050 im Browser und läuft, solange das
Terminal-Fenster offen ist.

- **Live:** Kurse aktualisieren sich automatisch jede Minute (Button ↻ für sofort).
  Krypto bewegt sich rund um die Uhr, Aktien/ETFs nur zu Börsenzeiten.
  Yahoo-Kurse sind wenige Minuten verzögert.
- **Werte hinzufügen:** Oben ins Suchfeld tippen (z. B. „dogecoin“, „rheinmetall“,
  „msci world“) und auf das Ergebnis klicken. Funktioniert für Krypto, Aktien weltweit,
  ETFs und Indizes. Deutsche Börsenplätze haben die Endung `.DE` (XETRA).
- **Entfernen:** Auf das × am Zeilenende klicken.
- Deine Liste wird in `watchlist.json` gespeichert.

### Aufbau

- **Start:** Investitionswelten (Kacheln), Auffälligkeiten, größte Bewegungen, Marktstimmung
- **🪙 Krypto / 🏢 Aktien / 🧺 ETFs & Indizes:** Tabelle der jeweiligen Welt mit allen Signalen
- **Einzelanalyse:** Namen anklicken → Signale mit Erklärung, Kursverlauf mit 50/200-Tage-Schnitt
  und RSI (1 Monat bis Max), Profil, Analysten, Auffälligkeiten, News, Strategie-Vergleich
- **🧪 Strategie-Vergleich** und **ℹ️ Datenquellen** (woher welche Zahl kommt)

### Auffälligkeiten & Benachrichtigungen

Der Radar prüft alle 5 Minuten im Hintergrund (solange er läuft) und schickt eine
**Mac-Benachrichtigung**, wenn etwas *neu* auffällig wird:

- RSI fällt unter 30 / steigt über 70
- Trendwechsel (Kurs kreuzt den 200-Tage-Schnitt)
- starke Tagesbewegung (Krypto ±8 %, Aktien ±5 %, ETFs ±3 %)
- neues Allzeithoch oder 30 / 50 / 70 % unter dem Hoch
- Fear & Greed unter 20 oder über 80, Bitcoin-MVRV unter 1 oder über 3, Mayer-Multiple

Schwellen anpassen: oben in `alerts.py`. Ein-/Ausschalten: Häkchen im Radar.
Falls keine Mitteilungen kommen: Systemeinstellungen → Mitteilungen → „Skripteditor“ erlauben.

### Benachrichtigungen aufs Handy (ntfy – empfohlen)

Im Radar bei „Auffälligkeiten“ auf **📱 Handy einrichten** klicken: App **ntfy** installieren
(iPhone/Android, kostenlos, kein Konto), in der App auf „+“ tippen, den angezeigten
Kanalnamen eingeben, „Test senden“. Der Kanalname steht in `.env` (`NTFY_TOPIC`) – nicht weitergeben.

### Radar auf dem Handy öffnen (gleiches WLAN)

Start → Auffälligkeiten → **„📲 Auf dem Handy öffnen“** zeigt einen QR-Code. Mit der Handy-Kamera scannen, Link öffnen,
dann „Zum Home-Bildschirm“ (iPhone/Safari) bzw. „Zum Startbildschirm hinzufügen“ (Android/Chrome) – dann sieht der Radar aus wie eine App.

- Geht nur, solange der Mac läuft und Handy + Mac im selben WLAN sind (nicht unterwegs).
- Andere Geräte brauchen einen geheimen Schlüssel (`RADAR_KEY` in `.env`), der im QR-Link steckt und sich danach im Handy-Browser merkt.
  Ohne Schlüssel sieht man nur eine Sperrseite. Über „Neuen Schlüssel erzeugen“ sperrst du alle bisherigen Handys aus.
- Wechselt die WLAN-Adresse des Macs (anderes Netz, Router-Neustart), den Code einmal neu scannen.
- Im Mobilfunk-Hotspot des Handys klappt es meist nicht – im normalen WLAN zuhause schon.

### Radar in der Cloud (GitHub) – läuft auch, wenn der Mac aus ist

GitHub rechnet den Radar etwa alle 15 Minuten neu (`.github/workflows/radar.yml` → `build_static.py`),
schickt neue Auffälligkeiten per ntfy aufs Handy und stellt die Seite auf GitHub Pages bereit.

- Die Cloud-Seite zeigt alles an (Start, Kategorien, Einzelanalyse, News, Strategie-Vergleich), Daten bis ~15 Min. alt.
- Werte hinzufügen/entfernen geht nur am Mac. Ist der Mac-Radar mit GitHub verbunden, lädt er die geänderte Liste
  automatisch hoch, und die Cloud-Seite rechnet neu.
- Der ntfy-Kanalname liegt als Geheimnis `NTFY_TOPIC` in den GitHub-Einstellungen (Settings → Secrets → Actions).
- Damit Meldungen nicht doppelt kommen: am Mac „Handy (ntfy)“ ausschalten, sobald die Cloud läuft.
- Lokal ausprobieren: `RADAR_TEST=1 .venv/bin/python build_static.py` (schickt nichts) → Ergebnis in `site/`.

### Depot, Preis-Alarme & Termine

- **Depot & Alarme** (Reiter am Mac): Käufe/Verkäufe eintragen, Gewinn/Verlust sehen; Preis-Alarme setzen
  (auch auf jeder Einzelseite). Gespeichert **verschlüsselt** in `private.enc` (Schlüssel `PRIVATE_KEY` in `.env`,
  in der Cloud `RADAR_PRIVATE_KEY`). Erscheint nie auf der öffentlichen Seite.
- **Signale**: RSI wird mit dem Trend kombiniert. ★ = überverkauft im Aufwärtstrend (stärker); überverkauft im
  Abwärtstrend ist nur ein Hinweis. „X % unter Allzeithoch“ wird nicht mehr gemeldet, nur angezeigt.
- **Quartalszahlen**: Termine auf der Startseite, auf der Einzelseite und als Meldung 2 Tage vorher.
- **Berichte**: Morgenbericht täglich ab 6 Uhr, Wochenrückblick sonntags ab 18 Uhr – als Bild mit Depot,
  Kacheln inkl. 30-Tage-Linie, Signalen und Terminen.

### Telegram-Befehle (Antwort beim nächsten Cloud-Lauf, bis ~15 Min.)

`/kurs btc` · `/liste` · `/add dogecoin` · `/weg dogecoin` · `/alarm btc < 70000` · `/alarme` · `/alarmweg 2`
· `/kauf btc 0,05 70000` · `/verkauf btc 0,02` · `/depot` · `/bericht` · `/hilfe`
Nur Nachrichten aus deinem eigenen Chat werden beachtet.

**Schlüssel in die Cloud bringen:** Doppelklick auf `Cloud-Schlüssel übertragen.command` (überträgt ntfy, Depot-Schlüssel
und Telegram aus `.env` als GitHub-Secrets).

### Alternativ: Telegram

Im Radar bei „Auffälligkeiten“ auf **📱 Handy einrichten** klicken und den 3 Schritten folgen
(Bot bei @BotFather anlegen → Token einfügen → Bot starten → Verbinden). Danach kommen alle
neuen Auffälligkeiten zusätzlich als Telegram-Nachricht. Token und Chat-ID liegen nur lokal
in `.env`. Nachrichten kommen, solange der Radar auf dem Mac läuft und der Mac wach ist.

### News

Klick auf einen Namen (in den Tabellen oder bei den Auffälligkeiten) zeigt aktuelle
Schlagzeilen dazu – auf Deutsch oder Englisch, über Google News.

| Bereich | Hauptsignale | Zusatzinfo |
|---|---|---|
| Krypto | Trend, RSI, Fear & Greed, Bitcoin-Zyklus (MVRV, Mayer, Halving) | Momentum, 1-Wochen-Umkehr |
| Aktien | Trend, RSI, Value (KGV, KBV), Qualität (EK-Rendite, Marge, Schulden) | Momentum, 1-Wochen-Umkehr, Shiller-KGV |
| ETFs/Indizes | Trend, RSI | Momentum, 1-Wochen-Umkehr |

Grün = dieser Indikator spricht historisch eher für einen Kauf, Rot = eher Vorsicht.
Datenquellen (kostenlos, ohne Anmeldung): Yahoo Finance, alternative.me,
CNN, multpl.com, CoinMetrics.

`radar.py` erzeugt weiterhin eine einmalige, statische Seite (`radar.html`), falls du
nur einen Schnappschuss brauchst.

## Strategie-Vergleich (Backtest)

Im Radar unter „Strategie-Vergleich“: Wert wählen → „Vergleich starten“.
Oder im Terminal für mehrere Werte auf einmal:

```bash
.venv/bin/python vergleich.py BTC-USD NVDA SPY
```

Getestet werden die Radar-Signale als feste Regeln – Kaufen & Halten, Trend, RSI 30/70,
Dip abwarten, Fear & Greed (Krypto), MVRV/Mayer (Bitcoin), Kombi – einmal als
Einmalanlage (10.000) und einmal als Sparplan (500/Monat). Mit Gebühren, ohne Blick in
die Zukunft, Schwellen nicht nachträglich optimiert. Aktien/ETFs ab 2015, Krypto ab 2018.
