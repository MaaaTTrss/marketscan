#!/bin/zsh
# Verbindet deinen Telegram-Bot mit dem Cloudflare-Worker (Schnell-Antworten).
# Doppelklick genügt. Der Bot-Token wird aus .env gelesen und nicht angezeigt.
cd "$(dirname "$0")"
TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' .env 2>/dev/null | head -1 | cut -d= -f2-)
if [ -z "$TOKEN" ]; then echo "Kein Telegram-Bot in .env gefunden – erst im Radar verbinden."; read; exit 1; fi

echo "=== MarketScan · Telegram-Schnellantworten ==="
echo
echo "1) Adresse deines Cloudflare-Workers einfügen (endet auf .workers.dev)"
echo "   Zum Ausschalten stattdessen einfach  aus  eingeben."
printf "> "; read URL
URL=$(echo "$URL" | tr -d ' ')

if [ "$URL" = "aus" ]; then
  curl -s "https://api.telegram.org/bot$TOKEN/deleteWebhook" >/dev/null && echo "✓ Ausgeschaltet – der Bot antwortet wieder im 15-Minuten-Takt."
  echo; echo "Fenster kann geschlossen werden."; read; exit 0
fi
case "$URL" in http*) ;; *) URL="https://$URL" ;; esac

echo
printf "   Prüfe den Worker … "
if curl -s -m 15 "$URL" | grep -q "MarketScan-Worker"; then echo "✓ erreichbar"; else echo "✗ antwortet nicht wie erwartet – Code eingefügt und auf Deploy geklickt?"; read; exit 1; fi

SECRET=$(openssl rand -hex 16)
echo
echo "2) Dieses Passwort im Worker als Secret  WEBHOOK_SECRET  speichern:"
echo
echo "      $SECRET"
echo
echo "   (Cloudflare → dein Worker → Settings → Variables and Secrets → Add → Typ: Secret)"
echo "   Danach hier Enter drücken."
read

R=$(curl -s "https://api.telegram.org/bot$TOKEN/setWebhook" \
  --data-urlencode "url=$URL" --data-urlencode "secret_token=$SECRET" \
  --data-urlencode 'allowed_updates=["message"]')
if echo "$R" | grep -q '"ok":true'; then
  echo "✓ Telegram schickt Nachrichten jetzt direkt an den Worker."
  echo
  echo "3) Test: schreib deinem Bot z. B.  /kurs btc  – die Antwort sollte in ~1 Minute kommen."
else
  echo "✗ Hat nicht geklappt: $R"
fi
echo; echo "Fenster kann geschlossen werden."; read
